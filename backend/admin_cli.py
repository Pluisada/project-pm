"""Provision administrators from the command line (run on the server).

The web app only ever creates one admin - the very first user, via /api/setup
- and every other account (admin-created or self-signed-up) is a member. This
script is the supported way to add or change admins afterwards. Being able to
run it (shell access to the server and its database) is the authorization
check: it's an operator tool, not something reachable over HTTP.

    uv run python admin_cli.py create  --username alice
    uv run python admin_cli.py promote --username alice
    uv run python admin_cli.py demote  --username e2e_admin

Passwords are never accepted as arguments (they'd land in shell history and
the process list): `create` prompts twice without echo, or reads one line from
stdin with --password-stdin. Nothing is printed except usernames and roles.
"""
import argparse
import getpass
import sys

from auth import hash_password
from database import SessionLocal, init_db
from models import User, UserRole

MIN_PASSWORD_LENGTH = 8  # same rule as the setup/signup schemas
MAX_PASSWORD_LENGTH = 128


class ProvisioningError(Exception):
    """A refused operation; the message is safe to show."""


def _get_user(db, username: str) -> User:
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise ProvisioningError(f'No user named "{username}"')
    return user


def _validate_password(password: str) -> None:
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise ProvisioningError(
            f"Password must be {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} characters"
        )


def create_admin(db, username: str, password: str) -> User:
    """Create a new admin. Refuses an existing username rather than taking
    over (or resetting the password of) someone else's account."""
    username = username.strip()
    if not 3 <= len(username) <= 50:
        raise ProvisioningError("Username must be 3-50 characters")
    if db.query(User).filter(User.username == username).first() is not None:
        raise ProvisioningError(f'User "{username}" already exists; use "promote" instead')
    _validate_password(password)

    user = User(username=username, password_hash=hash_password(password), role=UserRole.ADMIN.value)
    db.add(user)
    db.commit()
    return user


def promote(db, username: str) -> User:
    """Make an existing, named user an admin. Their password is unchanged."""
    user = _get_user(db, username)
    if user.role == UserRole.ADMIN.value:
        raise ProvisioningError(f'"{username}" is already an admin')
    user.role = UserRole.ADMIN.value
    db.commit()
    return user


def demote(db, username: str) -> User:
    """Make an admin a member. Refuses to remove the last admin, which would
    leave nobody able to manage users or the board structure."""
    user = _get_user(db, username)
    if user.role != UserRole.ADMIN.value:
        raise ProvisioningError(f'"{username}" is not an admin')
    admins = db.query(User).filter(User.role == UserRole.ADMIN.value).count()
    if admins <= 1:
        raise ProvisioningError("Refusing to demote the last admin; create or promote another first")
    user.role = UserRole.MEMBER.value
    db.commit()
    return user


def _read_password(from_stdin: bool, stdin=sys.stdin, prompt=getpass.getpass) -> str:
    if from_stdin:
        return stdin.readline().rstrip("\n")
    password = prompt("New admin password: ")
    if prompt("Repeat password: ") != password:
        raise ProvisioningError("Passwords do not match")
    return password


def _confirm(username: str, action: str, assume_yes: bool, ask=input) -> None:
    if assume_yes:
        return
    if ask(f'Type the username to {action} "{username}": ').strip() != username:
        raise ProvisioningError("Confirmation did not match; nothing changed")


def main(
    argv=None,
    session_factory=SessionLocal,
    init=init_db,
    stdin=sys.stdin,
    prompt=getpass.getpass,
    ask=input,
) -> int:
    """CLI entry point. The keyword arguments exist so tests can point it at
    an isolated database and script the prompts."""
    parser = argparse.ArgumentParser(description="Create, promote or demote PM administrators.")
    sub = parser.add_subparsers(dest="command", required=True)

    create_p = sub.add_parser("create", help="create a new admin account")
    create_p.add_argument("--username", required=True)
    create_p.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password from the first line of stdin instead of prompting",
    )
    for name, help_text in [("promote", "make an existing user an admin"), ("demote", "make an admin a member")]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--username", required=True)
        p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    args = parser.parse_args(argv)
    init()  # same startup step as the app (creates missing tables/columns)
    db = session_factory()
    try:
        if args.command == "create":
            password = _read_password(args.password_stdin, stdin, prompt)
            user = create_admin(db, args.username, password)
            print(f'Created admin "{user.username}".')
        elif args.command == "promote":
            _confirm(args.username, "promote", args.yes, ask)
            user = promote(db, args.username)
            print(f'"{user.username}" is now an admin.')
        else:
            _confirm(args.username, "demote", args.yes, ask)
            user = demote(db, args.username)
            print(f'"{user.username}" is now a member.')
    except ProvisioningError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
