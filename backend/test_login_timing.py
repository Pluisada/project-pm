"""Login runs the same bcrypt verification whether or not the account exists.

Deterministic: counts bcrypt.checkpw calls instead of measuring wall-clock time.
"""
import bcrypt
import pytest

import auth
from auth import UNUSABLE_PASSWORD_HASH, hash_password
from models import User
from auth_test_utils import VERIFIED
from test_rate_limit import client, login, make_client, session  # noqa: F401  (fixtures)


@pytest.fixture
def checkpw_calls(monkeypatch):
    calls = []
    real = bcrypt.checkpw

    def spy(password, hashed):
        calls.append(hashed)
        return real(password, hashed)

    monkeypatch.setattr(auth.bcrypt, "checkpw", spy)
    return calls


def test_unknown_user_and_wrong_password_both_run_one_bcrypt_check(client, checkpw_calls):
    assert login(client, "nobody", "Wrongpass123!").status_code == 401
    assert login(client, "admin", "Wrongpass123!").status_code == 401
    assert len(checkpw_calls) == 2


def test_unknown_user_is_checked_against_a_real_bcrypt_hash_at_the_normal_cost(client, checkpw_calls):
    login(client, "nobody", "Wrongpass123!")
    dummy = checkpw_calls[0]
    real_cost = hash_password("x").split("$")[2]
    assert dummy.decode().split("$")[2] == real_cost


def test_account_without_a_usable_password_also_runs_the_check(client, session, checkpw_calls):
    session.add(User(username="pending", email="pending@example.com", email_verified_at=None,
                     password_hash=UNUSABLE_PASSWORD_HASH, role="member"))
    session.commit()
    assert login(client, "pending", "Wrongpass123!").status_code == 401
    assert len(checkpw_calls) == 1


def test_dummy_hash_never_authenticates():
    assert auth.verify_login_password("anything", None) is False
    assert auth.verify_login_password("anything", UNUSABLE_PASSWORD_HASH) is False


def test_errors_stay_generic_and_valid_login_still_works(client):
    unknown = login(client, "nobody", "Wrongpass123!")
    wrong = login(client, "admin", "Wrongpass123!")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()
    assert login(client).status_code == 200
