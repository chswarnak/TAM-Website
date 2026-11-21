
import os
import boto3
from pathlib import Path
from dotenv import dotenv_values
from botocore.config import Config
import json
import tempfile
import threading

# TAM project configuration
ENV_FILE = Path(__file__).resolve().parent / ".env"

TAM_AWS_REGION = "ca-central-1"
TAM_S3_BUCKET = "tam-smart-event-photos"


def get_tam_aws_session():
    """
    Load TAM AWS credentials explicitly from .env.

    Do not silently use credentials from the
    developer's shell environment or AWS profile.
    """
    settings = dotenv_values(ENV_FILE)

    access_key = settings.get("AWS_ACCESS_KEY_ID")
    secret_key = settings.get("AWS_SECRET_ACCESS_KEY")
    session_token = settings.get("AWS_SESSION_TOKEN") or None

    if not access_key or not secret_key:
        if os.getenv("TAM_USE_IAM_ROLE") == "1":
            return boto3.Session(region_name=TAM_AWS_REGION)

        raise RuntimeError(
            "TAM AWS credentials are not configured."
        )

    return boto3.Session(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        aws_session_token=session_token,
        region_name=TAM_AWS_REGION
    )


def get_tam_s3_client():
    """
    Create an S3 client using TAM credentials.
    """
    session = get_tam_aws_session()

    return session.client(
        "s3",
        config=Config(
            connect_timeout=3,
            read_timeout=5,
            retries={"max_attempts": 1}
        )
    )

_aws_config_lock = threading.Lock()

AWS_SECRET_NAMES = {
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
}


def save_tam_aws_credentials(
    access_key: str,
    secret_key: str,
    session_token: str = ""
):
    access_key = access_key.strip()
    secret_key = secret_key.strip()
    session_token = session_token.strip()

    if not access_key or not secret_key:
        raise ValueError("AWS credentials are required.")

    for value in (access_key, secret_key, session_token):
        if "\n" in value or "\r" in value:
            raise ValueError("Invalid credential format.")

    with _aws_config_lock:
        if not ENV_FILE.is_file():
            raise RuntimeError("TAM .env file not found.")

        original = ENV_FILE.read_text(encoding="utf-8")
        preserved = []

        for line in original.splitlines():
            key = line.strip().split("=", 1)[0].strip()

            if key.startswith("export "):
                key = key[7:].strip()

            if key not in AWS_SECRET_NAMES:
                preserved.append(line)

        replacements = [
            f"AWS_ACCESS_KEY_ID={json.dumps(access_key)}",
            f"AWS_SECRET_ACCESS_KEY={json.dumps(secret_key)}",
        ]

        if session_token:
            replacements.append(
                f"AWS_SESSION_TOKEN={json.dumps(session_token)}"
            )

        updated = "\n".join(
            preserved + replacements
        ).rstrip() + "\n"

        temp_path = None

        try:
            fd, temp_path = tempfile.mkstemp(
                prefix=".tam-aws-",
                dir=str(ENV_FILE.parent)
            )

            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(updated)
                file.flush()
                os.fsync(file.fileno())

            os.chmod(temp_path, 0o600)
            os.replace(temp_path, ENV_FILE)

        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

