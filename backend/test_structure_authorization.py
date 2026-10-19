"""Board and column deletion: authorization boundary and blast radius.

Complements test_security.py (which covers the 401/403 status codes) by
asserting that denied requests leave every related row untouched, that a
revoked token cannot delete, and that an authorized delete cascades only to
its own board/column. Uses an in-memory SQLite database with foreign keys on.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth import create_user_access_token, hash_password
from auth_test_utils import VERIFIED
from database import enable_sqlite_foreign_keys, get_db
from main import app
from models import Base, Board, Card, Column, ConversationMessage, User, UserRole

TRACKED = (Board, Column, Card, ConversationMessage, User)


@pytest.fixture
def db():
    engine = enable_sqlite_foreign_keys(
        create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _user(db, username, role):
    user = User(
        username=username,
        password_hash=hash_password("Password123!"),
        role=role,
        email_verified_at=VERIFIED,
    )
    db.add(user)
    db.commit()
    return user


def _headers(user):
    return {"Authorization": f"Bearer {create_user_access_token(user)}"}


@pytest.fixture
def world(db):
    """Two boards, each with two columns and cards, a chat message, an admin and a member."""
    admin = _user(db, "boss", UserRole.ADMIN.value)
    member = _user(db, "rep", UserRole.MEMBER.value)
    boards = []
    for n in (1, 2):
        board = Board(user_id=admin.id, title=f"Board {n}")
        db.add(board)
        db.commit()
        cols = [Column(board_id=board.id, title=f"B{n}C{i}", position=i) for i in range(2)]
        db.add_all(cols)
        db.commit()
        for col in cols:
            db.add(Card(column_id=col.id, title=f"card in {col.title}", position=0))
        db.add(ConversationMessage(board_id=board.id, user_id=admin.id, role="user", content="hi"))
        db.commit()
        boards.append((board, cols))
    return {"admin": admin, "member": member, "boards": boards}


def _counts(db):
    return {model.__name__: db.query(model).count() for model in TRACKED}


def _assert_integrity(db):
    assert db.execute(text("PRAGMA foreign_key_check")).fetchall() == []
    assert db.execute(text("PRAGMA integrity_check")).scalar() == "ok"


class TestDeniedRequestsChangeNothing:
    @pytest.mark.parametrize("who", ["anonymous", "member", "bad-token", "garbage-header"])
    def test_denied_board_and_column_delete(self, client, db, world, who):
        (board, cols), _ = world["boards"]
        headers = {
            "anonymous": {},
            "member": _headers(world["member"]),
            "bad-token": {"Authorization": "Bearer not.a.jwt"},
            "garbage-header": {"Authorization": "Basic abc"},
        }[who]
        before = _counts(db)

        board_resp = client.delete(f"/api/boards/{board.id}", headers=headers)
        column_resp = client.delete(f"/api/boards/{board.id}/columns/{cols[0].id}", headers=headers)

        expected = 403 if who == "member" else 401
        assert board_resp.status_code == expected
        assert column_resp.status_code == expected
        assert _counts(db) == before
        _assert_integrity(db)

    def test_member_with_revoked_session_cannot_delete(self, client, db, world):
        """A token from before logout/password change must not reach the route at all."""
        (board, cols), _ = world["boards"]
        admin = world["admin"]
        headers = _headers(admin)
        admin.session_version += 1
        db.commit()
        before = _counts(db)

        assert client.delete(f"/api/boards/{board.id}", headers=headers).status_code == 401
        assert client.delete(f"/api/boards/{board.id}/columns/{cols[0].id}", headers=headers).status_code == 401
        assert _counts(db) == before

    def test_role_is_read_from_db_not_token(self, client, db, world):
        """An admin demoted after login loses delete rights with the same token."""
        (board, _), _ = world["boards"]
        admin = world["admin"]
        headers = _headers(admin)
        admin.role = UserRole.MEMBER.value
        db.commit()
        before = _counts(db)

        assert client.delete(f"/api/boards/{board.id}", headers=headers).status_code == 403
        assert _counts(db) == before

    def test_member_cannot_modify_structure_via_put(self, client, db, world):
        (board, cols), _ = world["boards"]
        headers = _headers(world["member"])
        assert client.put(f"/api/boards/{board.id}", json={"title": "x"}, headers=headers).status_code == 403
        assert (
            client.put(f"/api/boards/{board.id}/columns/{cols[0].id}", json={"title": "x"}, headers=headers).status_code
            == 403
        )
        db.expire_all()
        assert db.get(Board, board.id).title == "Board 1"
        assert db.get(Column, cols[0].id).title == "B1C0"


class TestAuthorizedDeleteBlastRadius:
    def test_admin_board_delete_only_removes_that_board(self, client, db, world):
        (board1, cols1), (board2, cols2) = world["boards"]

        assert client.delete(f"/api/boards/{board1.id}", headers=_headers(world["admin"])).status_code == 204

        assert db.get(Board, board1.id) is None
        assert db.query(Column).filter(Column.board_id == board1.id).count() == 0
        assert db.query(Card).filter(Card.column_id.in_([c.id for c in cols1])).count() == 0
        # The other board, its columns, cards and chat, and every user survive.
        assert db.get(Board, board2.id) is not None
        assert db.query(Column).filter(Column.board_id == board2.id).count() == 2
        assert db.query(Card).filter(Card.column_id.in_([c.id for c in cols2])).count() == 2
        assert db.query(ConversationMessage).filter(ConversationMessage.board_id == board2.id).count() == 1
        assert db.query(User).count() == 2
        _assert_integrity(db)

    def test_admin_column_delete_only_removes_that_column(self, client, db, world):
        (board1, cols1), (board2, _) = world["boards"]

        resp = client.delete(f"/api/boards/{board1.id}/columns/{cols1[0].id}", headers=_headers(world["admin"]))

        assert resp.status_code == 204
        assert db.get(Column, cols1[0].id) is None
        assert db.query(Card).filter(Card.column_id == cols1[0].id).count() == 0
        assert db.get(Column, cols1[1].id) is not None
        assert db.query(Card).filter(Card.column_id == cols1[1].id).count() == 1
        assert db.get(Board, board1.id) is not None
        assert db.query(Card).count() == 3
        assert db.query(Column).filter(Column.board_id == board2.id).count() == 2
        _assert_integrity(db)

    def test_column_delete_with_mismatched_board_is_404_and_deletes_nothing(self, client, db, world):
        """A column id from board 2 addressed through board 1 must not be deletable."""
        (board1, _), (_, cols2) = world["boards"]
        before = _counts(db)

        resp = client.delete(f"/api/boards/{board1.id}/columns/{cols2[0].id}", headers=_headers(world["admin"]))

        assert resp.status_code == 404
        assert _counts(db) == before

    def test_missing_board_or_column_is_404(self, client, db, world):
        (board1, _), _ = world["boards"]
        headers = _headers(world["admin"])
        before = _counts(db)

        assert client.delete("/api/boards/9999", headers=headers).status_code == 404
        assert client.delete(f"/api/boards/{board1.id}/columns/9999", headers=headers).status_code == 404
        assert client.delete("/api/boards/9999/columns/1", headers=headers).status_code == 404
        assert _counts(db) == before
