"""Central login endpoint, separate from the legacy /api/login.

Authenticates only against the central identity database (central_auth.authenticate)
and returns the "identity_access" token, which the legacy deps.get_current_user
rejects. The legacy users table, auth_tokens and company databases are not touched.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

import rate_limit
from central_auth import AuthenticationFailed, authenticate
from central_runtime import CentralIdentityRuntime, central_runtime


class CentralLoginRequest(BaseModel):
    email: str
    password: str


class CentralLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


def build_central_router(runtime: CentralIdentityRuntime) -> APIRouter:
    router = APIRouter(prefix="/api/central")

    @router.post("/login", response_model=CentralLoginResponse)
    def central_login(
        body: CentralLoginRequest,
        http_request: Request,
        session: Session = Depends(runtime.get_session),
    ) -> CentralLoginResponse:
        # Same limits as the legacy login, keyed apart so the two flows do not share delays.
        account = "central:" + rate_limit.identifier_key(body.email)
        ip = rate_limit.client_ip(http_request)
        rate_limit.hit("login_ip", ip)
        rate_limit.check_login_delay(account, ip)
        try:
            auth_session = authenticate(session, body.email, body.password)
        except AuthenticationFailed:
            rate_limit.record_login_failure(account, ip)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
            ) from None
        rate_limit.clear_login_failures(account, ip)
        return CentralLoginResponse(access_token=auth_session.access_token)

    return router


central_router = build_central_router(central_runtime)
