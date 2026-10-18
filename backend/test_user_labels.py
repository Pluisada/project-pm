"""reports.user_labels: read-only id -> label lookup in one company database (tmp_path databases only)."""
import hashlib

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import identity_service
from identity_models import IdentityBase, IdentityMembership, IdentityUser
from models import CompanyBase, CompanyUser
from reports import FORMER_USER_LABEL, user_labels


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def company(tmp_path):
    path = tmp_path / "company.db"
    engine = create_engine(f"sqlite:///{path}")
    CompanyBase.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as seed:
        seed.add_all([
            CompanyUser(id=3, username="ana"),
            CompanyUser(id=5, username="bob"),
            CompanyUser(id=9, username="bob"),  # same label, different person
        ])
        seed.commit()
    db = sessionmaker(bind=engine)()
    yield db, path, engine
    db.close()
    engine.dispose()


def test_existing_ids_resolve_to_username(company):
    db, _, _ = company
    assert user_labels(db, [3, 5]) == {3: "ana", 5: "bob"}


def test_missing_id_gets_fixed_fallback_and_none_is_not_an_id(company):
    db, _, _ = company
    labels = user_labels(db, [3, 404, None])
    assert labels == {3: "ana", 404: FORMER_USER_LABEL}
    assert FORMER_USER_LABEL == "Former user"
    assert None not in labels  # unassigned stays distinguishable from a missing user


def test_duplicate_labels_do_not_merge_ids(company):
    db, _, _ = company
    assert user_labels(db, [5, 9, 5]) == {5: "bob", 9: "bob"}


def test_empty_input_runs_no_query(company):
    db, _, engine = company
    statements = []
    event.listen(engine, "before_cursor_execute", lambda *a: statements.append(a[2]))
    assert user_labels(db, []) == {}
    assert user_labels(db, [None, None]) == {}
    assert user_labels(db, iter(())) == {}
    assert statements == []
    user_labels(db, [3])
    assert len(statements) == 1


def test_central_state_is_not_consulted(company, tmp_path, monkeypatch):
    db, _, _ = company
    identity_path = tmp_path / "identity.db"
    identity_engine = create_engine(f"sqlite:///{identity_path}")
    IdentityBase.metadata.create_all(identity_engine)
    with sessionmaker(bind=identity_engine)() as ident:
        ident.add(IdentityUser(id=3, email="ana@example.com", is_active=False))
        ident.flush()
        ident.add(IdentityMembership(user_id=3, company_id=1, role="sales_member", is_active=False))
        ident.commit()
    identity_engine.dispose()
    before = digest(identity_path)

    def boom(*a, **k):
        raise AssertionError("central state consulted")

    monkeypatch.setattr(identity_service, "company_access", boom)
    monkeypatch.setattr(identity_service, "sync_company_user", boom)
    assert user_labels(db, [3]) == {3: "ana"}
    assert digest(identity_path) == before


def test_lookup_leaves_company_database_unchanged(company):
    db, path, engine = company
    before = digest(path)
    user_labels(db, [3, 5, 9, 404])
    assert not db.new and not db.dirty and not db.deleted
    db.rollback()
    engine.dispose()
    assert digest(path) == before
    assert db.query(CompanyUser).count() == 3  # no stub was created for 404
