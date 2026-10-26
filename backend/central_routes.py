"""Central login endpoint, separate from the legacy /api/login.

Authenticates only against the central identity database (central_auth.authenticate)
and returns the "identity_access" token, which the legacy deps.get_current_user
rejects. The legacy users table, auth_tokens and company databases are not touched.
"""
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

import central_auth as ca
import branded_email
import mailer
import rate_limit
from auth import APP_BASE_URL
from central_auth import AuthenticationFailed, authenticate
from central_deps import bearer_token, require_matching_company_header
from central_runtime import CentralIdentityRuntime, central_runtime
from database_targets import COMPANY_SLUGS

logger = logging.getLogger(__name__)
GENERIC_FORGOT = "If the account exists, a link to choose a new password has been sent."


class CentralLoginRequest(BaseModel):
    email: str
    password: str


class ForgotRequest(BaseModel):
    email: str


class ResetRequest(BaseModel):
    token: str
    new_password: str


class AcceptInvitationRequest(BaseModel):
    token: str
    password: str


class JoinInvitationRequest(BaseModel):
    token: str
    email: str
    password: str


class InviteRequest(BaseModel):
    email: str
    role: str


class RoleChangeRequest(BaseModel):
    role: str


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

    def current_user(authorization: Optional[str] = Header(None), session: Session = Depends(runtime.get_session)):
        try:
            return ca.user_for_token(session, bearer_token(authorization))
        except AuthenticationFailed:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token",
                headers={"WWW-Authenticate": "Bearer"},
            ) from None

    @router.get("/me")
    def me(user=Depends(current_user), session: Session = Depends(runtime.get_session)):
        """The signed-in person and the companies they may enter (central database only)."""
        return {"email": user.email, "is_global_admin": user.is_global_admin,
                "companies": ca.list_company_access(session, user)}

    @router.post("/logout", status_code=204)
    def logout(user=Depends(current_user), session: Session = Depends(runtime.get_session)):
        ca.logout(session, user.id)

    @router.post("/password/forgot")
    def forgot(body: ForgotRequest, http_request: Request, background: BackgroundTasks,
               session: Session = Depends(runtime.get_session)):
        """Always the same answer; the mail is sent after the response and only for an eligible account."""
        rate_limit.hit("forgot_ip", rate_limit.client_ip(http_request))
        account = "central:" + rate_limit.identifier_key(body.email)
        if rate_limit.try_hit("forgot_account", account):
            raw = ca.issue_password_reset(session, body.email)
            if raw is not None:
                message = branded_email.build_password_reset_email(f"{APP_BASE_URL}/?central_reset_token={raw}")
                mailer.send_in_background(background, body.email.strip(), message.subject, message.text,
                                          message.html, message.inline_images)
        return {"message": GENERIC_FORGOT}

    @router.post("/password/reset")
    def reset(body: ResetRequest, http_request: Request, session: Session = Depends(runtime.get_session)):
        rate_limit.hit("forgot_ip", rate_limit.client_ip(http_request))
        try:
            ca.reset_password(session, body.token, body.new_password)
        except ca.WeakPassword as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except ca.ResetTokenInvalid:
            raise HTTPException(status_code=400, detail="This link is invalid or has expired") from None
        return {"message": "Password updated. Sign in with the new password."}

    @router.post("/invitations/accept")
    def accept(body: AcceptInvitationRequest, http_request: Request, session: Session = Depends(runtime.get_session)):
        """First password for an invited person; membership and account are created in one transaction."""
        rate_limit.hit("signup_ip", rate_limit.client_ip(http_request))
        try:
            ca.accept_invitation_with_password(session, body.token, body.password)
        except ca.WeakPassword as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except ca.InvitationInvalid:
            raise HTTPException(status_code=400, detail="This invitation is invalid or has expired") from None
        return {"message": "Account ready. Sign in with your email and password."}

    @router.post("/invitations/join")
    def join(body: JoinInvitationRequest, http_request: Request, session: Session = Depends(runtime.get_session)):
        """An existing account accepts an invitation to another company: it proves who it is with its own password.

        Same login throttling as /login. The invitation must have been issued to this account's email
        (identity_service.accept_invitation); every refusal on the invitation side answers the same way.
        """
        account = "central:" + rate_limit.identifier_key(body.email)
        ip = rate_limit.client_ip(http_request)
        rate_limit.hit("signup_ip", ip)
        rate_limit.check_login_delay(account, ip)
        try:
            ca.join_invitation(session, body.token, body.email, body.password)
        except AuthenticationFailed:
            rate_limit.record_login_failure(account, ip)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials") from None
        except ca.InvitationInvalid:
            rate_limit.clear_login_failures(account, ip)  # the password was right; only the invitation is not
            raise HTTPException(status_code=400, detail="This invitation is invalid or has expired") from None
        rate_limit.clear_login_failures(account, ip)
        return {"message": "You have joined the company. It now appears in your company list."}

    @router.post("/companies/{company_slug}/invitations", status_code=202, dependencies=[Depends(require_matching_company_header)])
    def invite(company_slug: str, body: InviteRequest,
               user=Depends(current_user), session: Session = Depends(runtime.get_session)):
        """Global administrators only. The link goes to the invitee by email and is never returned."""
        if company_slug not in COMPANY_SLUGS or not user.is_global_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        try:
            issued = ca.create_invitation(session, user.id, body.email, company_slug, body.role)
        except ca.IdentityServiceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        message = _invitation_message(session, company_slug, issued.token)
        # The invitation exists either way; the answer says whether the email actually went out. Admin-only, so the
        # synchronous send leaks nothing about which addresses are registered.
        if not mailer.is_configured():
            return {"email_sent": False, "message": "Invitation created, but email delivery is not configured, so nothing "
                    "was sent. Use Resend on the pending invitation once mail works."}
        try:
            mailer.send_email(body.email.strip(), message.subject, message.text, message.html, message.inline_images)
        except mailer.EmailDeliveryError:
            return {"email_sent": False, "message": "Invitation created, but the email could not be sent. "
                    "Use Resend on the pending invitation to try again."}
        return {"email_sent": True, "message": "Invitation sent."}

    @router.post("/companies/{company_slug}/invitations/{invitation_id}/resend", dependencies=[Depends(require_matching_company_header)])
    def resend_invitation(company_slug: str, invitation_id: int, user=Depends(current_user),
                          session: Session = Depends(runtime.get_session)):
        """Global administrators only. Delivers a new link for a still-open invitation and, only once the mail was
        handed to the SMTP server, retires the previous link. Answers honestly: no SMTP is 503, a failed send is 502
        (the previous link is untouched). The link is never returned."""
        if company_slug not in COMPANY_SLUGS or not user.is_global_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        if not mailer.is_configured():
            raise HTTPException(status_code=503, detail="Email delivery is not configured; the invitation was not sent")
        resend = _managed(ca.begin_invitation_resend, session, user.id, company_slug, invitation_id)
        try:
            message = _invitation_message(session, company_slug, resend.token)
            mailer.send_email(resend.email, message.subject, message.text, message.html, message.inline_images)
        except mailer.EmailDeliveryError:
            ca.finish_invitation_resend(session, user.id, resend, delivered=False)
            raise HTTPException(status_code=502, detail="The email could not be sent; try again later") from None
        ca.finish_invitation_resend(session, user.id, resend, delivered=True)
        return {"message": "Invitation email sent."}

    def _invitation_message(session: Session, company_slug: str, token: str) -> branded_email.BrandedEmail:
        """Branding comes from the company of the invitation (slug already validated server-side), never the request."""
        company = ca.get_company(session, company_slug)
        return branded_email.build_invitation_email(
            company.slug, company.name, f"{APP_BASE_URL}/?central_invite_token={token}", APP_BASE_URL)

    def _managed(call, *args, **kwargs):
        """Run a membership service call; every refusal is a fixed-message HTTP error."""
        try:
            return call(*args, **kwargs)
        except ca.NotAuthorized:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied") from None
        except ca.NotFound:
            raise HTTPException(status_code=404, detail="Not found") from None
        except ca.Conflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except ca.IdentityServiceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @router.get("/companies/{company_slug}/members", dependencies=[Depends(require_matching_company_header)])
    def members(company_slug: str, user=Depends(current_user), session: Session = Depends(runtime.get_session)):
        return _managed(ca.list_members, session, user.id, company_slug)

    @router.get("/companies/{company_slug}/invitations", dependencies=[Depends(require_matching_company_header)])
    def pending_invitations(company_slug: str, user=Depends(current_user),
                            session: Session = Depends(runtime.get_session)):
        return _managed(ca.list_pending_invitations, session, user.id, company_slug)

    @router.post("/users/{user_id}/cancel", status_code=204)
    def cancel_user(user_id: int, user=Depends(current_user), session: Session = Depends(runtime.get_session)):
        """Global administrators only. Permanently cancels a central identity in every company (irreversible); the
        commercial history stays. Not company-scoped, unlike the per-company deactivation below."""
        if not user.is_global_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        _managed(ca.cancel_user, session, user.id, user_id)

    @router.post("/companies/{company_slug}/members/{user_id}/deactivate", status_code=204, dependencies=[Depends(require_matching_company_header)])
    def deactivate_member(company_slug: str, user_id: int, user=Depends(current_user),
                          session: Session = Depends(runtime.get_session)):
        _managed(ca.deactivate_membership, session, user.id, company_slug, user_id)

    @router.put("/companies/{company_slug}/members/{user_id}/role", status_code=204, dependencies=[Depends(require_matching_company_header)])
    def change_role(company_slug: str, user_id: int, body: RoleChangeRequest, user=Depends(current_user),
                    session: Session = Depends(runtime.get_session)):
        _managed(ca.change_membership_role, session, user.id, company_slug, user_id, body.role)

    return router


central_router = build_central_router(central_runtime)
