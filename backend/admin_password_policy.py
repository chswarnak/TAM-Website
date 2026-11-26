"""
Shared password policy for TAM administrator accounts.
Applies when creating or changing administrator passwords.
"""

import re

POLICY_MESSAGE = (
    "Password must be 8-128 characters and contain "
    "at least one uppercase letter, one lowercase letter, "
    "one number, and one special character."
)


def validate_admin_password(password: str) -> None:
    """Raise ValueError when the password does not meet policy."""

    if not isinstance(password, str):
        raise ValueError(POLICY_MESSAGE)

    if not 8 <= len(password) <= 128:
        raise ValueError(POLICY_MESSAGE)

    if not re.search(r"[A-Z]", password):
        raise ValueError(POLICY_MESSAGE)

    if not re.search(r"[a-z]", password):
        raise ValueError(POLICY_MESSAGE)

    if not re.search(r"[0-9]", password):
        raise ValueError(POLICY_MESSAGE)

    if not re.search(r"[^A-Za-z0-9]", password):
        raise ValueError(POLICY_MESSAGE)
