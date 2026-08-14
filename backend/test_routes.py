"""Tests for API routes."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth import create_access_token, hash_password
from main import app
from database import get_db
from models import Base, User, UserRole, Board, Column, Card


@pytest.fixture(scope="function")
def test_db():
    """Create test database."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()

    # Create test user (member is enough - board routes have no role gate)
    user = User(
        username="test_user",
        password_hash=hash_password("test_password"),
        full_name="Test User",
        role=UserRole.MEMBER.value,
    )
    session.add(user)
    session.commit()

    yield session

    session.close()


@pytest.fixture
def client(test_db):
    """Create test client with test database, authenticated as test_user."""
    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)
    token = create_access_token(data={"sub": "test_user"})
    test_client.headers.update({"Authorization": f"Bearer {token}"})
    yield test_client
    app.dependency_overrides.clear()


class TestUnauthenticatedAccess:
    """Board routes must reject requests without a valid token."""

    def test_list_boards_requires_auth(self, test_db):
        """Regression test: routes.py used to silently bypass the token check."""

        def override_get_db():
            yield test_db

        app.dependency_overrides[get_db] = override_get_db
        try:
            response = TestClient(app).get("/api/boards")
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 401


class TestBoardRoutes:
    """Test board endpoints."""

    def test_list_boards_empty(self, client):
        """Test listing boards when none exist."""
        response = client.get("/api/boards")
        assert response.status_code == 200
        assert response.json() == []

    def test_create_board(self, client):
        """Test creating a board."""
        payload = {
            "title": "Test Board",
            "description": "A test board"
        }
        response = client.post("/api/boards", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "Test Board"
        assert data["description"] == "A test board"
        assert "id" in data
        assert "user_id" in data

    def test_create_board_missing_title(self, client):
        """Test creating board without title."""
        payload = {"description": "No title"}
        response = client.post("/api/boards", json=payload)
        assert response.status_code == 422

    def test_list_boards_after_create(self, client):
        """Test listing boards after creating one."""
        payload = {"title": "Board 1"}
        client.post("/api/boards", json=payload)

        response = client.get("/api/boards")
        assert response.status_code == 200
        boards = response.json()
        assert len(boards) == 1
        assert boards[0]["title"] == "Board 1"

    def test_get_board_detail(self, client, test_db):
        """Test getting board with columns and cards."""
        # Create board
        board = Board(user_id=1, title="Test Board")
        test_db.add(board)
        test_db.commit()

        # Create columns
        for i, title in enumerate(["Backlog", "In Progress", "Done"]):
            col = Column(board_id=board.id, title=title, position=i)
            test_db.add(col)
        test_db.commit()

        # Create cards
        cards_data = [
            (1, "Card 1", "Details 1", 0),
            (1, "Card 2", "Details 2", 1),
            (2, "Card 3", "Details 3", 0),
        ]
        for col_id, title, details, pos in cards_data:
            card = Card(column_id=col_id, title=title, details=details, position=pos)
            test_db.add(card)
        test_db.commit()

        # Get board
        response = client.get(f"/api/boards/{board.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Test Board"
        assert len(data["columns"]) == 3
        assert len(data["columns"][0]["cards"]) == 2
        assert len(data["columns"][1]["cards"]) == 1

    def test_get_board_not_found(self, client):
        """Test getting non-existent board."""
        response = client.get("/api/boards/9999")
        assert response.status_code == 404


class TestColumnRoutes:
    """Test column endpoints."""

    def test_create_column(self, client, test_db):
        """Test creating a column."""
        board = Board(user_id=1, title="Test")
        test_db.add(board)
        test_db.commit()

        payload = {"title": "To Do", "position": 0}
        response = client.post(f"/api/boards/{board.id}/columns", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "To Do"
        assert data["position"] == 0

    def test_update_column_title(self, client, test_db):
        """Test updating column title."""
        board = Board(user_id=1, title="Test")
        test_db.add(board)
        test_db.commit()

        col = Column(board_id=board.id, title="Old Title", position=0)
        test_db.add(col)
        test_db.commit()

        payload = {"title": "New Title"}
        response = client.put(f"/api/boards/{board.id}/columns/{col.id}", json=payload)
        assert response.status_code == 200
        assert response.json()["title"] == "New Title"

    def test_delete_column(self, client, test_db):
        """Test deleting column."""
        board = Board(user_id=1, title="Test")
        test_db.add(board)
        test_db.commit()

        col = Column(board_id=board.id, title="To Delete", position=0)
        test_db.add(col)
        test_db.commit()

        response = client.delete(f"/api/boards/{board.id}/columns/{col.id}")
        assert response.status_code == 204


class TestCardRoutes:
    """Test card endpoints."""

    def test_create_card(self, client, test_db):
        """Test creating a card."""
        board = Board(user_id=1, title="Test")
        test_db.add(board)
        test_db.commit()

        col = Column(board_id=board.id, title="To Do", position=0)
        test_db.add(col)
        test_db.commit()

        payload = {
            "column_id": col.id,
            "title": "New Task",
            "details": "Task details",
            "position": 0
        }
        response = client.post(f"/api/boards/{board.id}/cards", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "New Task"
        assert data["details"] == "Task details"

    def test_update_card(self, client, test_db):
        """Test updating a card."""
        board = Board(user_id=1, title="Test")
        test_db.add(board)
        test_db.commit()

        col = Column(board_id=board.id, title="To Do", position=0)
        test_db.add(col)
        test_db.commit()

        card = Card(column_id=col.id, title="Old Title", details="Old", position=0)
        test_db.add(card)
        test_db.commit()

        payload = {"title": "Updated Title", "details": "Updated details"}
        response = client.put(f"/api/boards/{board.id}/cards/{card.id}", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Updated Title"
        assert data["details"] == "Updated details"

    def test_move_card(self, client, test_db):
        """Test moving card to different column."""
        board = Board(user_id=1, title="Test")
        test_db.add(board)
        test_db.commit()

        col1 = Column(board_id=board.id, title="To Do", position=0)
        col2 = Column(board_id=board.id, title="Done", position=1)
        test_db.add(col1)
        test_db.add(col2)
        test_db.commit()

        card = Card(column_id=col1.id, title="Task", details="", position=0)
        test_db.add(card)
        test_db.commit()

        payload = {"column_id": col2.id, "position": 0}
        response = client.put(f"/api/boards/{board.id}/cards/{card.id}/position", json=payload)
        assert response.status_code == 200
        assert response.json()["column_id"] == col2.id

    def test_delete_card(self, client, test_db):
        """Test deleting a card."""
        board = Board(user_id=1, title="Test")
        test_db.add(board)
        test_db.commit()

        col = Column(board_id=board.id, title="To Do", position=0)
        test_db.add(col)
        test_db.commit()

        card = Card(column_id=col.id, title="Delete Me", details="", position=0)
        test_db.add(card)
        test_db.commit()

        response = client.delete(f"/api/boards/{board.id}/cards/{card.id}")
        assert response.status_code == 204


class TestSharedBoardAccess:
    """Boards are shared: any authenticated user can see/edit any board."""

    def test_board_created_by_one_user_visible_to_another(self, test_db, client):
        """A board isn't scoped to whoever created it."""
        creator = test_db.query(User).filter(User.username == "test_user").first()
        board = Board(user_id=creator.id, title="Shared Board")
        test_db.add(board)
        test_db.commit()

        other = User(
            username="other_user",
            password_hash=hash_password("other_password"),
            role=UserRole.MEMBER.value,
        )
        test_db.add(other)
        test_db.commit()

        other_token = create_access_token(data={"sub": "other_user"})
        response = client.get(
            f"/api/boards/{board.id}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert response.status_code == 200
        assert response.json()["title"] == "Shared Board"

    def test_list_boards_returns_all_boards(self, test_db, client):
        """list_boards is not filtered by owner."""
        test_db.add(Board(user_id=1, title="Board A"))
        test_db.add(Board(user_id=1, title="Board B"))
        test_db.commit()

        response = client.get("/api/boards")
        assert response.status_code == 200
        assert len(response.json()) == 2


class TestUserManagementRoutes:
    """Test admin-only user management endpoints."""

    @pytest.fixture
    def admin_headers(self, test_db):
        """Promote an admin user and return auth headers for them."""
        admin = User(
            username="admin_user",
            password_hash=hash_password("adminpass123"),
            role=UserRole.ADMIN.value,
        )
        test_db.add(admin)
        test_db.commit()
        token = create_access_token(data={"sub": "admin_user"})
        return {"Authorization": f"Bearer {token}"}

    def test_non_admin_cannot_list_users(self, client):
        """A member (the default client fixture) gets 403."""
        response = client.get("/api/users")
        assert response.status_code == 403

    def test_non_admin_cannot_create_user(self, client):
        """A member cannot create new users."""
        response = client.post(
            "/api/users", json={"username": "new_member", "password": "password123"}
        )
        assert response.status_code == 403

    def test_admin_can_list_users(self, client, admin_headers):
        """An admin can list existing users."""
        response = client.get("/api/users", headers=admin_headers)
        assert response.status_code == 200
        usernames = {u["username"] for u in response.json()}
        assert "test_user" in usernames
        assert "admin_user" in usernames

    def test_admin_can_create_member_user(self, client, admin_headers):
        """An admin creates a new user, who is always a member."""
        response = client.post(
            "/api/users",
            json={"username": "new_member", "password": "password123"},
            headers=admin_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["username"] == "new_member"
        assert data["role"] == "member"
        assert "password" not in data
        assert "password_hash" not in data

    def test_admin_cannot_create_duplicate_username(self, client, admin_headers):
        """Creating a user with a taken username is rejected."""
        response = client.post(
            "/api/users",
            json={"username": "test_user", "password": "password123"},
            headers=admin_headers,
        )
        assert response.status_code == 409

    def test_new_member_can_login_and_see_shared_board(self, test_db, client, admin_headers):
        """A member created by the admin can log in and use the shared board."""
        test_db.add(Board(user_id=1, title="Shared Board"))
        test_db.commit()

        client.post(
            "/api/users",
            json={"username": "new_member", "password": "password123"},
            headers=admin_headers,
        )

        login_response = client.post(
            "/api/login", json={"username": "new_member", "password": "password123"}
        )
        assert login_response.status_code == 200
        token = login_response.json()["access_token"]

        boards_response = client.get(
            "/api/boards", headers={"Authorization": f"Bearer {token}"}
        )
        assert boards_response.status_code == 200
        assert len(boards_response.json()) == 1
