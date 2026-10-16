import hmac
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import or_
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from auth import (
    APP_BASE_URL,
    E2E_ONLY_USERNAMES,
    RESET_TOKEN_EXPIRE_MINUTES,
    SIGNUP_TOKEN_EXPIRE_HOURS,
    LoginRequest,
    LoginResponse,
    create_user_access_token,
    hash_password,
    verify_login_password,
)
from database import E2E_MODE, get_db, init_db, seed_default_board
from deps import get_current_user
from stage_history import SOURCE_SEED, set_stage_context
import ai_limits
import http_security
import production_config
import rate_limit
import signup_policy
import token_store
from mailer import EmailDeliveryError, send_email
from models import User, UserRole
from routes import router as api_router
from import_routes import REVERT_RETRY_AFTER_SECONDS, router as import_router
from bid_import_revert import is_lock_error
from central_routes import central_router
from central_runtime import central_runtime
from company_routes import company_router
from company_runtime import company_runtime
from schemas import (
    ForgotPasswordRequest,
    ResetPasswordRequest,
    SetupRequest,
    SetupStatusResponse,
    SignupCompleteRequest,
    SignupRequest,
)

app = FastAPI(title="PM Backend", version="0.1.0", **production_config.docs_kwargs())
logger = logging.getLogger(__name__)

_LINK_TOKEN = re.compile(r"((?:signup|reset)_token=)[^&\s\"]+")


class _RedactLinkTokens(logging.Filter):
    """Uvicorn's access log prints the full request path, and the emailed links
    open `/?signup_token=...` / `/?reset_token=...`; keep those tokens out of it."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _LINK_TOKEN.sub(r"\1[redacted]", a) if isinstance(a, str) else a for a in record.args
            )
        return True


logging.getLogger("uvicorn.access").addFilter(_RedactLinkTokens())


async def database_error_handler(request: Request, exc: OperationalError) -> JSONResponse:
    """A SQLite BUSY/LOCKED that no route handled (e.g. during authentication) is a 503 with
    Retry-After; any other OperationalError is a 500. Neither body carries SQL or driver text."""
    if is_lock_error(exc):
        if "imports" in request.url.path.split("/"):
            detail = {"code": "import_busy", "message": "The database is busy; nothing was changed. Try again shortly.", "applied": False}
        else:
            detail = {"code": "database_busy", "message": "The database is busy. Try again shortly."}
        return JSONResponse({"detail": detail}, status_code=503, headers={"Retry-After": REVERT_RETRY_AFTER_SECONDS})
    logger.error("Unhandled database error (%s)", type(exc).__name__)
    return JSONResponse({"detail": "Internal Server Error"}, status_code=500)


# Initialize database on startup
@app.on_event("startup")
async def startup_event():
    """Initialize database tables on startup. Users/boards are created via /api/setup."""
    production_config.validate_config()
    rate_limit.validate_config()
    signup_policy.validate_config()
    ai_limits.validate_config()
    http_security.validate_config()
    init_db()


@app.on_event("shutdown")
async def shutdown_event():
    """Release the central identity engine if a central request opened it."""
    central_runtime.dispose()
    company_runtime.dispose()

app.add_middleware(ai_limits.ChatBodyLimitMiddleware)
app.add_exception_handler(RequestValidationError, ai_limits.chat_validation_handler)
app.add_exception_handler(OperationalError, database_error_handler)

# CORS: explicit origins only (CORS_ALLOWED_ORIGINS, default the local dev
# frontend). Auth is a Bearer header, not cookies, so no credentials. Added
# last so the security headers also cover preflight and error responses.
app.add_middleware(
    CORSMiddleware,
    allow_origins=http_security.cors_origins(),
    allow_credentials=False,
    allow_methods=http_security.CORS_METHODS,
    allow_headers=http_security.CORS_HEADERS,
)
app.add_middleware(http_security.SecurityHeadersMiddleware)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _find_user(db: Session, identifier: str) -> User | None:
    """Look a user up by username, or by email case-insensitively.

    Self-signed-up users have their email stored as both username and email.
    """
    return (
        db.query(User)
        .filter(or_(User.username == identifier, User.email == identifier.lower()))
        .first()
    )


# Authentication endpoints
@app.post("/api/login", response_model=LoginResponse)
async def login(request: LoginRequest, http_request: Request, db: Session = Depends(get_db)):
    """Login endpoint - returns JWT token."""
    # Limits apply before the lookup and are keyed on what was typed, so a
    # delayed account and an unknown name look the same. The delay is per
    # (account, client address), never account-wide.
    account = rate_limit.identifier_key(request.username)
    ip = rate_limit.client_ip(http_request)
    rate_limit.hit("login_ip", ip)
    rate_limit.check_login_delay(account, ip)
    user = _find_user(db, request.username)
    # Unverified accounts and the public E2E accounts fail exactly like a wrong password.
    # verify_login_password runs one bcrypt check even for unknown users, so timing does not reveal them.
    if (
        not verify_login_password(request.password, user.password_hash if user else None)
        or not user
        or user.email_verified_at is None
        or (user.username in E2E_ONLY_USERNAMES and not E2E_MODE)
    ):
        rate_limit.record_login_failure(account, ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    rate_limit.clear_login_failures(account, ip)
    access_token = create_user_access_token(user)

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        username=user.username,
        role=user.role,
    )


def _bump_session_version(db: Session, user_id: int) -> None:
    """Atomically revoke every access token of the user. Caller commits."""
    db.execute(update(User).where(User.id == user_id).values(session_version=User.session_version + 1))


@app.post("/api/logout")
async def logout(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Log out: revokes ALL of this user's sessions (every device), because the
    session version is per user. The token used here is already dead afterwards."""
    _bump_session_version(db, current_user.id)
    db.commit()
    return {"message": "Logged out successfully", "username": current_user.username}


# API health check endpoint
@app.get("/api/health")
async def health():
    """Health check endpoint. test_mode is true only in the isolated E2E
    backend (PM_E2E=1), which the Playwright setup asserts before any test."""
    return {"status": "ok", "test_mode": E2E_MODE}


# Protected endpoint (for testing auth)
@app.get("/api/user")
async def get_user(current_user: User = Depends(get_current_user)):
    """Get current user info (protected)."""
    return {
        "username": current_user.username,
        "role": current_user.role,
        "authenticated": True,
    }


# Setup endpoints: bootstrap the first (admin) user
@app.get("/api/setup/status", response_model=SetupStatusResponse)
async def setup_status(db: Session = Depends(get_db)):
    """Whether the system has no users yet, and whether HTTP setup is switched on."""
    return SetupStatusResponse(
        needs_setup=db.query(User).first() is None,
        setup_enabled=_configured_setup_token() is not None,
    )


SETUP_TOKEN_MIN_LENGTH = 32


def _configured_setup_token() -> str | None:
    """The operator-provided setup key, or None (setup disabled) if unset or too short."""
    token = os.getenv("PM_SETUP_TOKEN", "")
    return token if len(token) >= SETUP_TOKEN_MIN_LENGTH else None


@app.post("/api/setup", response_model=LoginResponse, status_code=201)
async def setup(
    request: SetupRequest,
    http_request: Request,
    db: Session = Depends(get_db),
):
    """Create the first user as admin.

    Fails closed: disabled unless the operator sets PM_SETUP_TOKEN (at least 32
    characters) and the caller presents it in X-Setup-Token. Even then it only
    works while no users exist, so it shuts itself once the admin is created."""
    expected = _configured_setup_token()
    if expected is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Setup is disabled")
    rate_limit.hit("login_ip", rate_limit.client_ip(http_request))
    presented = http_request.headers.get("x-setup-token", "")
    if not hmac.compare_digest(presented.encode(), expected.encode()):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid setup key")

    if db.query(User).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Setup already completed",
        )

    if request.username in E2E_ONLY_USERNAMES and not E2E_MODE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reserved username")

    user = User(
        username=request.username,
        password_hash=hash_password(request.password),
        role=UserRole.ADMIN.value,
        email_verified_at=_utcnow(),  # the operator doing first-run setup has server access
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    set_stage_context(db, user, SOURCE_SEED)
    seed_default_board(db, user)

    access_token = create_user_access_token(user)

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        username=user.username,
        role=user.role,
    )


# Self-signup: request an emailed link, then set a password from it
SIGNUP_SENT_MESSAGE = "If the address can be registered, a link was sent"
SIGNUP_NOT_ALLOWED_MESSAGE = "Sign-up is not available for this email address"


def _require_signup_allowed(email: str) -> None:
    """403 unless the email domain is in SIGNUP_ALLOWED_DOMAINS or the address in SIGNUP_ALLOWED_EMAILS."""
    if not signup_policy.is_email_allowed(email):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=SIGNUP_NOT_ALLOWED_MESSAGE)


@app.post("/api/signup", status_code=202)
async def signup(
    request: SignupRequest,
    http_request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Email a verification link for setting a password. Creates no user yet."""
    # Counted before the existence check, so registered and new addresses
    # hit the same limits; the email bucket also stops mail-bombing one inbox.
    rate_limit.hit("signup_ip", rate_limit.client_ip(http_request))
    rate_limit.hit("signup_email", rate_limit.identifier_key(request.email))
    email = request.email.lower()
    _require_signup_allowed(email)
    if db.query(User).first() is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Setup not completed",
        )

    # Same response whether or not the address is registered, so the endpoint
    # can't be used to probe which accounts exist.
    if _find_user(db, email) is None:
        raw = token_store.issue(db, token_store.PURPOSE_SIGNUP, timedelta(hours=SIGNUP_TOKEN_EXPIRE_HOURS), email=email)
        db.commit()
        _send_in_background(
            background,
            email,
            "Complete your PM sign-up",
            f"Open this link to verify your email and set your password:\n\n{APP_BASE_URL}/?signup_token={raw}"
            f"\n\nThe link expires in {SIGNUP_TOKEN_EXPIRE_HOURS} hours and works once.",
        )
    return {"message": SIGNUP_SENT_MESSAGE}


def _send_in_background(background: BackgroundTasks, to: str, subject: str, body: str) -> None:
    """Deliver after the response, so latency doesn't reveal whether mail was sent.
    A delivery failure is already logged (type only) by the mailer."""

    def deliver():
        try:
            send_email(to, subject, body)
        except EmailDeliveryError:
            pass

    background.add_task(deliver)


@app.post("/api/signup/complete", status_code=201)
async def signup_complete(request: SignupCompleteRequest, db: Session = Depends(get_db)):
    """Create a member account for the email bound to a valid signup token.

    Consuming the link is the email verification: the account exists, already
    verified, only after this succeeds."""
    token = token_store.consume(db, request.token, token_store.PURPOSE_SIGNUP)
    if token is None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired link",
        )
    email = token.email
    # Re-checked here: the allowlist may have changed since the link was sent.
    try:
        _require_signup_allowed(email)
        if _find_user(db, email) is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account already exists")
        db.add(
            User(
                username=email,
                email=email,
                password_hash=hash_password(request.password),
                role=UserRole.MEMBER.value,
                email_verified_at=_utcnow(),
            )
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account already exists")
    except HTTPException:
        db.rollback()  # the token stays unused
        raise
    return {"message": "Account created"}


# Password reset: the response never says whether the account exists.
RESET_SENT_MESSAGE = "If an account exists for that address, a reset link was sent"


@app.post("/api/password/forgot")
async def forgot_password(
    request: ForgotPasswordRequest,
    http_request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Email a password reset link if the identifier names an account with an email.

    Unknown identifiers, accounts without an email and real accounts all get the
    same response; the mail itself is sent after the response."""
    rate_limit.hit("forgot_ip", rate_limit.client_ip(http_request))
    user = _find_user(db, request.identifier.strip())
    # The per-account quota is spent only by an eligible reset, and a request over
    # it gets the same answer as any other, so typing someone's name can neither
    # use up their quota nor reveal whether the account exists.
    if user is not None and user.email and rate_limit.try_hit("forgot_account", str(user.id)):
        raw = token_store.issue(db, token_store.PURPOSE_RESET, timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES), user_id=user.id)
        db.commit()
        _send_in_background(
            background,
            user.email,
            "Reset your PM password",
            f"Open this link to choose a new password:\n\n{APP_BASE_URL}/?reset_token={raw}\n\n"
            f"The link expires in {RESET_TOKEN_EXPIRE_MINUTES // 60} hour and works once. "
            "If you didn't ask for this, ignore this email.",
        )
    return {"message": RESET_SENT_MESSAGE}


@app.post("/api/password/reset")
async def reset_password(request: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Set a new password from a valid, unused reset token.

    The token is consumed in the same transaction as the change, which also ends
    every existing session. Following the link proves control of the mailbox, so
    it verifies the email (this is how an account provisioned without a password
    gets its first one)."""
    token = token_store.consume(db, request.token, token_store.PURPOSE_RESET)
    user = db.get(User, token.user_id) if token else None
    if user is None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired link",
        )

    user.password_hash = hash_password(request.password)
    if user.email_verified_at is None:
        user.email_verified_at = _utcnow()
    _bump_session_version(db, user.id)
    token_store.invalidate_for_user(db, user.id)
    db.commit()
    return {"message": "Password updated"}


# Include API routes
app.include_router(api_router)
app.include_router(import_router)
app.include_router(central_router)
app.include_router(company_router)


# Serve static frontend files at root
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
