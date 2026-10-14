"""Security regression tests: structural authorization, SECRET_KEY startup
checks, admin provisioning, and the AI diagnostic endpoint.

All database access goes to in-memory SQLite (and conftest.py points the
module-level engine at a temp file), never the development pm.db.
"""
import io
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import admin_cli
import auth
import routes
from ai_kanban import apply_board_actions
from auth import create_signup_token, create_user_access_token, hash_password, verify_password
from database import get_db
from main import app
from models import Base, Board, Column, User, UserRole

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def client(test_db):
    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _user(db, username, role):
    user = User(username=username, password_hash=hash_password("password123"), role=role)
    db.add(user)
    db.commit()
    return user


def _headers(user):
    return {"Authorization": f"Bearer {create_user_access_token(user)}"}


@pytest.fixture
def admin(test_db):
    return _headers(_user(test_db, "boss", UserRole.ADMIN.value))


@pytest.fixture
def member(test_db):
    return _headers(_user(test_db, "rep", UserRole.MEMBER.value))


@pytest.fixture
def board(test_db):
    board = Board(user_id=1, title="Pipeline")
    test_db.add(board)
    test_db.commit()
    column = Column(board_id=board.id, title="Award", position=0)
    test_db.add(column)
    test_db.commit()
    return board, column


# ---------------------------------------------------------------------------
# Structural authorization: board and columns are admin-only to change
# ---------------------------------------------------------------------------


class TestMemberCannotChangeStructure:
    def test_member_cannot_create_column(self, client, member, board, test_db):
        b, _ = board
        response = client.post(f"/api/boards/{b.id}/columns", json={"title": "New"}, headers=member)
        assert response.status_code == 403
        assert test_db.query(Column).count() == 1

    def test_member_cannot_rename_column(self, client, member, board, test_db):
        b, col = board
        response = client.put(
            f"/api/boards/{b.id}/columns/{col.id}", json={"title": "Won-ish"}, headers=member
        )
        assert response.status_code == 403
        test_db.refresh(col)
        assert col.title == "Award"

    def test_member_cannot_reorder_column(self, client, member, board):
        b, col = board
        response = client.put(
            f"/api/boards/{b.id}/columns/{col.id}", json={"position": 5}, headers=member
        )
        assert response.status_code == 403

    def test_member_cannot_delete_column(self, client, member, board, test_db):
        b, col = board
        response = client.delete(f"/api/boards/{b.id}/columns/{col.id}", headers=member)
        assert response.status_code == 403
        assert test_db.get(Column, col.id) is not None

    def test_member_cannot_create_board(self, client, member, test_db):
        response = client.post("/api/boards", json={"title": "Mine"}, headers=member)
        assert response.status_code == 403
        assert test_db.query(Board).count() == 0

    def test_member_cannot_update_board(self, client, member, board, test_db):
        b, _ = board
        response = client.put(f"/api/boards/{b.id}", json={"title": "Hijacked"}, headers=member)
        assert response.status_code == 403
        test_db.refresh(b)
        assert b.title == "Pipeline"

    def test_member_cannot_delete_board(self, client, member, board, test_db):
        b, _ = board
        response = client.delete(f"/api/boards/{b.id}", headers=member)
        assert response.status_code == 403
        assert test_db.get(Board, b.id) is not None

    @pytest.mark.parametrize(
        "method,path",
        [
            ("post", "/api/boards"),
            ("put", "/api/boards/{b}"),
            ("delete", "/api/boards/{b}"),
            ("post", "/api/boards/{b}/columns"),
            ("put", "/api/boards/{b}/columns/{c}"),
            ("delete", "/api/boards/{b}/columns/{c}"),
        ],
    )
    def test_unauthenticated_structural_writes_rejected(self, client, board, method, path):
        b, col = board
        response = getattr(client, method)(
            path.format(b=b.id, c=col.id), **({"json": {"title": "x"}} if method != "delete" else {})
        )
        assert response.status_code == 401

    def test_member_can_still_read_board(self, client, member, board):
        b, _ = board
        assert client.get(f"/api/boards/{b.id}", headers=member).status_code == 200
        assert client.get("/api/boards", headers=member).status_code == 200


class TestAdminCanChangeStructure:
    def test_admin_column_crud(self, client, admin, board, test_db):
        b, col = board
        created = client.post(f"/api/boards/{b.id}/columns", json={"title": "New"}, headers=admin)
        assert created.status_code == 201
        renamed = client.put(
            f"/api/boards/{b.id}/columns/{col.id}", json={"title": "Award 2"}, headers=admin
        )
        assert renamed.status_code == 200
        deleted = client.delete(f"/api/boards/{b.id}/columns/{created.json()['id']}", headers=admin)
        assert deleted.status_code == 204

    def test_admin_board_crud(self, client, admin, board, test_db):
        b, _ = board
        assert client.post("/api/boards", json={"title": "Second"}, headers=admin).status_code == 201
        assert client.put(f"/api/boards/{b.id}", json={"title": "Renamed"}, headers=admin).status_code == 200
        assert client.delete(f"/api/boards/{b.id}", headers=admin).status_code == 204
        assert test_db.get(Board, b.id) is None


class TestNoAlternativePath:
    @pytest.mark.asyncio
    async def test_ai_cannot_perform_structural_actions(self, test_db, board):
        """The AI path only knows card actions; structural ones are refused."""
        b, col = board
        rep = _user(test_db, "rep2", UserRole.MEMBER.value)
        results = await apply_board_actions(
            test_db,
            b.id,
            [
                {"type": "delete_column", "column_id": col.id},
                {"type": "rename_column", "column_id": col.id, "title": "Hacked"},
                {"type": "delete_board", "board_id": b.id},
            ],
            rep,
        )
        assert results["successful"] == []
        assert [f["reason"] for f in results["failed"]] == ["Unsupported action type"] * 3
        test_db.refresh(col)
        assert col.title == "Award"
        assert test_db.get(Board, b.id) is not None

    def test_member_card_rules_unchanged(self, client, member, board, test_db):
        """Card create stays open to members; card delete stays admin-only."""
        b, col = board
        card = client.post(
            f"/api/boards/{b.id}/cards", json={"column_id": col.id, "title": "Lead"}, headers=member
        )
        assert card.status_code == 201
        delete = client.delete(f"/api/boards/{b.id}/cards/{card.json()['id']}", headers=member)
        assert delete.status_code == 403


# ---------------------------------------------------------------------------
# SECRET_KEY: refuse to start without a safe key
# ---------------------------------------------------------------------------


def _import_app_with(secret):
    """Import the app in a fresh interpreter with the given SECRET_KEY."""
    env = {k: v for k, v in os.environ.items() if k != "SECRET_KEY"}
    if secret is not None:
        env["SECRET_KEY"] = secret
    return subprocess.run(
        [sys.executable, "-c", "import main"],
        cwd=BACKEND_DIR,
        env=env,  # keeps conftest's temp DATABASE_URL: nothing touches pm.db
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestSecretKeyStartup:
    def test_startup_refused_without_key(self):
        result = _import_app_with(None)
        assert result.returncode != 0
        assert "SECRET_KEY is not set" in result.stderr

    def test_startup_refused_with_empty_key(self):
        result = _import_app_with("   ")
        assert result.returncode != 0
        assert "SECRET_KEY is not set" in result.stderr

    @pytest.mark.parametrize("insecure", sorted(auth.INSECURE_SECRET_KEYS))
    def test_startup_refused_with_known_default(self, insecure):
        result = _import_app_with(insecure)
        assert result.returncode != 0
        assert "known insecure default" in result.stderr

    def test_startup_refused_with_short_key_without_echoing_it(self):
        short = "abc123-short-key"
        result = _import_app_with(short)
        assert result.returncode != 0
        assert "shorter than" in result.stderr
        assert short not in result.stderr + result.stdout

    def test_startup_succeeds_with_valid_key(self):
        result = _import_app_with(os.environ["SECRET_KEY"])
        assert result.returncode == 0, result.stderr

    def test_tokens_from_another_key_are_rejected(self, client, member, test_db):
        """Changing the key invalidates every previously signed token.

        The forged token is otherwise fully valid (right user, current `pwh`,
        future expiry), so only the signing key can be what rejects it.
        """
        from datetime import datetime, timedelta, timezone

        from jose import jwt

        user = test_db.query(User).filter(User.username == "rep").one()
        claims = {
            "sub": "rep",
            "pwh": auth.password_fingerprint(user.password_hash),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        }
        headers = lambda key: {"Authorization": "Bearer " + jwt.encode(claims, key, algorithm="HS256")}
        # Control: the same claims signed with the real key are accepted.
        assert client.get("/api/user", headers=headers(auth.SECRET_KEY)).status_code == 200
        forged = headers("dev-secret-key-change-in-production")
        assert client.get("/api/user", headers=forged).status_code == 401


# ---------------------------------------------------------------------------
# No admin through public or member-facing endpoints
# ---------------------------------------------------------------------------


class TestNoPublicAdmin:
    def test_signup_ignores_role(self, client, admin, test_db):
        response = client.post(
            "/api/signup/complete",
            json={"token": create_signup_token("x@example.com"), "password": "password123", "role": "admin"},
        )
        assert response.status_code == 201
        assert test_db.query(User).filter(User.username == "x@example.com").one().role == "member"

    def test_admin_created_users_are_members_even_if_asked(self, client, admin, test_db):
        response = client.post(
            "/api/users", json={"username": "newbie", "password": "password123", "role": "admin"}, headers=admin
        )
        assert response.status_code == 201
        assert response.json()["role"] == "member"

    def test_setup_cannot_run_twice(self, client, admin):
        response = client.post("/api/setup", json={"username": "intruder", "password": "password123"})
        assert response.status_code == 409


# ---------------------------------------------------------------------------
# Admin provisioning (admin_cli.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def factory(test_db):
    return lambda: test_db


def _run_cli(argv, factory, password_lines=(), confirm="", stdin_text=""):
    prompts = iter(password_lines)
    return admin_cli.main(
        argv,
        session_factory=factory,
        init=lambda: None,
        stdin=io.StringIO(stdin_text),
        prompt=lambda _msg: next(prompts),
        ask=lambda _msg: confirm,
    )


def _stored(db, username):
    """Re-read a user: main() closes the session it was given, detaching any
    objects loaded before the call."""
    return db.query(User).filter(User.username == username).one()


class TestAdminProvisioning:
    def test_create_admin_with_prompted_password(self, test_db, factory, capsys):
        secret = "s3cure-pass-for-test"
        assert _run_cli(["create", "--username", "alice"], factory, [secret, secret]) == 0
        user = test_db.query(User).filter(User.username == "alice").one()
        assert user.role == "admin"
        assert verify_password(secret, user.password_hash)
        out = capsys.readouterr()
        assert secret not in out.out + out.err

    def test_create_admin_from_stdin(self, test_db, factory):
        assert _run_cli(
            ["create", "--username", "bob", "--password-stdin"], factory, stdin_text="piped-pass-123\n"
        ) == 0
        assert verify_password("piped-pass-123", test_db.query(User).filter(User.username == "bob").one().password_hash)

    def test_mismatched_passwords_refused(self, test_db, factory):
        assert _run_cli(["create", "--username", "carol"], factory, ["one-password", "another-one"]) == 1
        assert test_db.query(User).count() == 0

    def test_short_password_refused(self, test_db, factory):
        assert _run_cli(["create", "--username", "dan"], factory, ["short", "short"]) == 1
        assert test_db.query(User).count() == 0

    def test_create_refuses_existing_user_and_keeps_their_password(self, test_db, factory):
        old_hash = _user(test_db, "rep", UserRole.MEMBER.value).password_hash
        assert _run_cli(["create", "--username", "rep"], factory, ["takeover-pass", "takeover-pass"]) == 1
        rep = _stored(test_db, "rep")
        assert rep.role == "member" and rep.password_hash == old_hash

    def test_promote_requires_matching_confirmation(self, test_db, factory):
        _user(test_db, "rep", UserRole.MEMBER.value)
        assert _run_cli(["promote", "--username", "rep"], factory, confirm="someone-else") == 1
        assert _stored(test_db, "rep").role == "member"

        assert _run_cli(["promote", "--username", "rep"], factory, confirm="rep") == 0
        assert _stored(test_db, "rep").role == "admin"

    def test_promote_unknown_user_refused(self, test_db, factory):
        assert _run_cli(["promote", "--username", "ghost", "--yes"], factory) == 1
        assert test_db.query(User).count() == 0

    def test_demote_refuses_last_admin(self, test_db, factory):
        _user(test_db, "boss", UserRole.ADMIN.value)
        assert _run_cli(["demote", "--username", "boss", "--yes"], factory) == 1
        assert _stored(test_db, "boss").role == "admin"

    def test_replace_test_admin_procedure(self, test_db, factory):
        """The documented swap: create a real admin, then demote the old one."""
        _user(test_db, "e2e_admin", UserRole.ADMIN.value)
        assert _run_cli(["create", "--username", "owner"], factory, ["owner-pass-123"] * 2) == 0
        assert _run_cli(["demote", "--username", "e2e_admin", "--yes"], factory) == 0
        assert _stored(test_db, "e2e_admin").role == "member"
        assert _stored(test_db, "owner").role == "admin"

    def test_password_is_not_an_argument(self):
        with pytest.raises(SystemExit):
            admin_cli.main(["create", "--username", "eve", "--password", "x"], init=lambda: None)


# ---------------------------------------------------------------------------
# POST /api/ai/test: admin only, rejected before any paid call
# ---------------------------------------------------------------------------


class TestAiTestEndpoint:
    @pytest.fixture
    def openrouter_calls(self, monkeypatch):
        calls = []

        async def fake_connectivity(question):
            calls.append(question)
            return {"success": True, "response": "4"}

        monkeypatch.setattr(routes, "test_ai_connectivity", fake_connectivity)
        return calls

    def test_unauthenticated_rejected_without_calling_openrouter(self, client, openrouter_calls):
        assert client.post("/api/ai/test").status_code == 401
        assert openrouter_calls == []

    def test_invalid_token_rejected_without_calling_openrouter(self, client, openrouter_calls):
        response = client.post("/api/ai/test", headers={"Authorization": "Bearer not-a-token"})
        assert response.status_code == 401
        assert openrouter_calls == []

    def test_member_forbidden_without_calling_openrouter(self, client, member, openrouter_calls):
        assert client.post("/api/ai/test", headers=member).status_code == 403
        assert openrouter_calls == []

    def test_admin_allowed(self, client, admin, openrouter_calls):
        response = client.post("/api/ai/test", headers=admin)
        assert response.status_code == 200
        assert len(openrouter_calls) == 1
