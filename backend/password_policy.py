"""The one password policy, applied by every endpoint and CLI that sets a password."""

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128

POLICY_DESCRIPTION = (
    f"Password must be {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} characters and include an "
    "uppercase letter, a lowercase letter, a number and a symbol"
)


def password_problems(password: str) -> list[str]:
    """What the password lacks; empty when it meets the policy."""
    problems = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        problems.append(f"at most {MAX_PASSWORD_LENGTH} characters")
    if not any(c.isupper() for c in password):
        problems.append("an uppercase letter")
    if not any(c.islower() for c in password):
        problems.append("a lowercase letter")
    if not any(c.isdecimal() for c in password):
        problems.append("a number")
    if not any(not c.isalnum() and not c.isspace() for c in password):
        problems.append("a symbol")
    return problems


def validate_password(password: str) -> str:
    """Return the password, or raise ValueError naming what is missing (never the value)."""
    problems = password_problems(password)
    if problems:
        raise ValueError(POLICY_DESCRIPTION)
    return password
