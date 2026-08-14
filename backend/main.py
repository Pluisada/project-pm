import os
from datetime import timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    LoginRequest,
    LoginResponse,
    create_access_token,
    hash_password,
    verify_password,
)
from database import get_db, init_db, seed_default_board
from deps import get_current_user
from models import User, UserRole
from routes import router as api_router
from schemas import SetupRequest, SetupStatusResponse

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


# Authentication endpoints
@app.post("/api/login", response_model=LoginResponse)
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    """Login endpoint - returns JWT token."""
    user = db.query(User).filter(User.username == request.username).first()
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
    """Health check endpoint."""
    return {"status": "ok"}


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
