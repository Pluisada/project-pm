import os
from datetime import timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import or_
from sqlalchemy.orm import Session

from auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    APP_BASE_URL,
    RESET_TOKEN_EXPIRE_MINUTES,
    LoginRequest,
    LoginResponse,
    create_access_token,
    create_purpose_token,
    create_signup_token,
    decode_purpose_token,
    hash_password,
    password_fingerprint,
    verify_password,
    verify_signup_token,
)
from database import E2E_MODE, get_db, init_db, seed_default_board
from deps import get_current_user
from mailer import send_email
from models import User, UserRole
from routes import router as api_router
from schemas import (
    ForgotPasswordRequest,
    ResetPasswordRequest,
    SetupRequest,
    SetupStatusResponse,
    SignupCompleteRequest,
    SignupRequest,
)

app = FastAPI(title="PM Backend", version="0.1.0")

# Initialize database on startup
@app.on_event("startup")
async def startup_event():
    """Initialize database tables on startup. Users/boards are created via /api/setup."""
    init_db()

# CORS middleware for frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    """Login endpoint - returns JWT token."""
    user = _find_user(db, request.username)
    if not user or not verify_password(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        username=user.username,
        role=user.role,
    )


@app.post("/api/logout")
async def logout(current_user: User = Depends(get_current_user)):
    """Logout endpoint - client should discard token."""
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
    """Whether the system still needs an admin account created."""
    return SetupStatusResponse(needs_setup=db.query(User).first() is None)


@app.post("/api/setup", response_model=LoginResponse, status_code=201)
async def setup(request: SetupRequest, db: Session = Depends(get_db)):
    """Create the first user as admin. Only works while no users exist."""
    if db.query(User).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Setup already completed",
        )

    user = User(
        username=request.username,
        password_hash=hash_password(request.password),
        role=UserRole.ADMIN.value,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    seed_default_board(db, user)

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        username=user.username,
        role=user.role,
    )


# Self-signup: request an emailed link, then set a password from it
SIGNUP_SENT_MESSAGE = "If the address can be registered, a link was sent"


@app.post("/api/signup", status_code=202)
async def signup(request: SignupRequest, db: Session = Depends(get_db)):
    """Email a link for setting a password. Creates no user yet."""
    if db.query(User).first() is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Setup not completed",
        )

    email = request.email.lower()
    # Same response whether or not the address is registered, so the endpoint
    # can't be used to probe which accounts exist.
    if _find_user(db, email) is not None:
        return {"message": SIGNUP_SENT_MESSAGE}

    link = f"{APP_BASE_URL}/?signup_token={create_signup_token(email)}"
    try:
        send_email(
            email,
            "Complete your PM sign-up",
            f"Open this link to set your password:\n\n{link}\n\nThe link expires in 24 hours.",
        )
    except OSError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not send email",
        )
    return {"message": SIGNUP_SENT_MESSAGE}


@app.post("/api/signup/complete", status_code=201)
async def signup_complete(request: SignupCompleteRequest, db: Session = Depends(get_db)):
    """Create a member account for the email in a valid signup token."""
    email = verify_signup_token(request.token)
    if email is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired link",
        )
    # Also what makes a link single-use: the second attempt finds the account.
    if _find_user(db, email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account already exists",
        )

    db.add(
        User(
            username=email,
            email=email,
            password_hash=hash_password(request.password),
            role=UserRole.MEMBER.value,
        )
    )
    db.commit()
    return {"message": "Account created"}


# Password reset: unlike signup, this deliberately says whether the account
# exists (requested behavior; it does allow probing for registered accounts).
@app.post("/api/password/forgot")
async def forgot_password(request: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Email a password reset link to the account's address."""
    user = _find_user(db, request.identifier.strip())
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No account found for that username or email",
        )
    if not user.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This account has no email address. Ask an admin to reset it.",
        )

    token = create_purpose_token(
        user.username,
        "reset",
        timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES),
        pwh=password_fingerprint(user.password_hash),
    )
    link = f"{APP_BASE_URL}/?reset_token={token}"
    try:
        send_email(
            user.email,
            "Reset your PM password",
            f"Open this link to choose a new password:\n\n{link}\n\n"
            "The link expires in 1 hour. If you didn't ask for this, ignore this email.",
        )
    except OSError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not send email",
        )
    return {"message": "Reset link sent", "email": user.email}


@app.post("/api/password/reset")
async def reset_password(request: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Set a new password from a valid, not-yet-used reset token."""
    payload = decode_purpose_token(request.token, "reset")
    user = (
        db.query(User).filter(User.username == payload["sub"]).first() if payload else None
    )
    # A mismatched fingerprint means the password changed since the link was
    # issued - including by this very link, which is what makes it single-use.
    if user is None or payload.get("pwh") != password_fingerprint(user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired link",
        )

    user.password_hash = hash_password(request.password)
    db.commit()
    return {"message": "Password updated"}


# Include API routes
app.include_router(api_router)


# Serve static frontend files at root
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
