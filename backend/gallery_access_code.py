"""
TAM Gallery — Event Access-Code Security.

Store salted password-style hashes, never plaintext access codes.
"""

import hashlib
import hmac
import secrets


ITERATIONS = 600_000


def hash_gallery_access_code(code: str) -> str:
    """Create a salted PBKDF2-HMAC-SHA256 hash."""

    if not isinstance(code, str) or not 12 <= len(code) <= 128:
        raise ValueError("Access code must contain 12–128 characters")

    salt = secrets.token_bytes(16)

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        code.encode("utf-8"),
        salt,
        ITERATIONS,
    )

    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_gallery_access_code(code: str, stored_hash: str) -> bool:
    """Check a supplied access code against its stored hash."""

    if not isinstance(code, str) or not 1 <= len(code) <= 128:
        return False

    if not stored_hash or not isinstance(stored_hash, str):
        return False

    try:
        algorithm, iterations_text, salt_hex, digest_hex = (
            stored_hash.split("$")
        )

        if algorithm != "pbkdf2_sha256":
            return False

        iterations = int(iterations_text)

        if iterations != ITERATIONS:
            return False

        salt = bytes.fromhex(salt_hex)
        expected_digest = bytes.fromhex(digest_hex)

        if len(salt) != 16 or len(expected_digest) != 32:
            return False

        actual_digest = hashlib.pbkdf2_hmac(
            "sha256",
            code.encode("utf-8"),
            salt,
            iterations,
        )

        return hmac.compare_digest(actual_digest, expected_digest)

    except (ValueError, TypeError, AttributeError):
        return False


# TAM-GALLERY-HASH-FORMAT-CHECK-V1

def is_valid_gallery_access_code_hash(stored_hash: str) -> bool:
    """
    Validate the structure of a stored gallery access-code hash.

    Does not verify an access code or recover the original secret.
    """

    import re

    if not isinstance(stored_hash, str):
        return False

    pattern = (
        r"pbkdf2_sha256\$"
        r"600000\$"
        r"[0-9a-f]{32}\$"
        r"[0-9a-f]{64}"
    )

    return re.fullmatch(pattern, stored_hash) is not None
