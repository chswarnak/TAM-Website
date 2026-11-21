
import boto3
from pathlib import Path
from aws_config import get_tam_s3_client

S3_BUCKET = "tam-smart-event-photos"
AWS_REGION = "ca-central-1"




def upload_photo_to_s3(
    event_id,
    contributor_id,
    local_file_path
):
    """
    Upload a local event photograph to its contributor
    folder in the TAM S3 bucket.
    """
    local_file = Path(local_file_path)

    if not local_file.is_file():
        raise FileNotFoundError(
            f"Photo not found: {local_file}"
        )

    contributor_folder = (
        f"contributor-{int(contributor_id):03d}"
    )

    s3_key = (
        f"{event_id}/{contributor_folder}/{local_file.name}"
    )

    
    get_tam_s3_client().upload_file(
        str(local_file),
        S3_BUCKET,
        s3_key
    )


    return {
        "bucket": S3_BUCKET,
        "key": s3_key
    }
