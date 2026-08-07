import os
from datetime import timedelta
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    LoginRequest,
    LoginResponse,
    create_access_token,
    verify_credentials,
    verify_token,
)
from database import init_db, create_sample_data
from routes import router as api_router

app = FastAPI(title="PM Backend", version="0.1.0")

# Initialize database on startup
@app.on_event("startup")
async def startup_event():
    """Initialize database and sample data on startup."""
    init_db()
    create_sample_data()

# CORS middleware for frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Dependency to get current user from token
async def get_current_user(authorization: Optional[str] = None) -> str:
    """Get current user from Authorization header."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Parse "Bearer <token>" format
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = parts[1]
    username = verify_token(token)
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return username


# Authentication endpoints
@app.post("/api/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """Login endpoint - returns JWT token."""
    if not verify_credentials(request.username, request.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": request.username}, expires_delta=access_token_expires
    )

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        username=request.username,
    )


@app.post("/api/logout")
async def logout(current_user: str = Depends(get_current_user)):
    """Logout endpoint - client should discard token."""
    return {"message": "Logged out successfully", "username": current_user}


# API health check endpoint
@app.get("/api/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


# Protected endpoint example (for testing auth)
@app.get("/api/user")
async def get_user(current_user: str = Depends(get_current_user)):
    """Get current user info (protected)."""
    return {"username": current_user, "authenticated": True}


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
