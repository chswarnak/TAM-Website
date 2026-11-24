"""In-memory stand-ins for S3 and Rekognition.

Fake "faces" are solid colours: a photo's people are the distinct non-white
colours at the centre of its four quadrants, and a selfie's face is its
centre colour. That lets tests (and the local demo) build photos in which
specific people appear and check that a selfie finds exactly those photos.
"""

import uuid
from io import BytesIO

from botocore.exceptions import ClientError
from PIL import Image

WHITE = (255, 255, 255)


def make_photo(*colours, size=(400, 300)) -> bytes:
    """JPEG with up to four 'people' (one colour per quadrant)."""
    image = Image.new("RGB", size, WHITE)
    width, height = size
    boxes = [
        (0, 0, width // 2, height // 2),
        (width // 2, 0, width, height // 2),
        (0, height // 2, width // 2, height),
        (width // 2, height // 2, width, height),
    ]
    for colour, box in zip(colours, boxes):
        image.paste(colour, box)
    output = BytesIO()
    image.save(output, format="JPEG", quality=95)
    return output.getvalue()


def make_selfie(colour) -> bytes:
    return make_photo(colour, colour, colour, colour)


def _nearest(colour, palette):
    return min(palette, key=lambda c: sum((a - b) ** 2 for a, b in zip(colour, c)))


PALETTE = [WHITE, (255, 0, 0), (0, 0, 255), (0, 160, 0), (255, 200, 0)]


def people_in(image_bytes: bytes) -> list:
    with Image.open(BytesIO(image_bytes)) as image:
        image = image.convert("RGB")
        width, height = image.size
        points = [(width // 4, height // 4), (3 * width // 4, height // 4),
                  (width // 4, 3 * height // 4), (3 * width // 4, 3 * height // 4)]
        found = []
        for point in points:
            colour = _nearest(image.getpixel(point), PALETTE)
            if colour != WHITE and colour not in found:
                found.append(colour)
        return found


class FakeS3:
    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}

    def upload_photo_to_s3(self, event_id, contributor_id, local_file_path):
        from pathlib import Path
        path = Path(local_file_path)
        key = f"{event_id}/contributor-{int(contributor_id):03d}/{path.name}"
        self.objects[("tam-smart-event-photos", key)] = path.read_bytes()
        return {"bucket": "tam-smart-event-photos", "key": key}

    # boto3-style client interface
    def get_object(self, Bucket, Key):
        return {"Body": BytesIO(self.objects[(Bucket, Key)])}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)


class FakeRekognition:
    def __init__(self, s3: FakeS3, collections=("tam-diwali-2026-faces",)):
        self.s3 = s3
        self.collections = set(collections)
        self.face_people: dict[str, tuple] = {}
        self.created: list[str] = []

    def _missing(self, collection_id):
        if collection_id not in self.collections:
            raise ClientError(
                {"Error": {"Code": "ResourceNotFoundException",
                           "Message": "collection not found"}},
                "IndexFaces",
            )

    def create_collection(self, CollectionId):
        self.collections.add(CollectionId)
        self.created.append(CollectionId)
        return {"StatusCode": 200}

    def index_faces(self, CollectionId, Image, **_):
        self._missing(CollectionId)
        obj = Image["S3Object"]
        data = self.s3.objects[(obj["Bucket"], obj["Name"])]
        records = []
        for person in people_in(data):
            face_id = str(uuid.uuid4())
            self.face_people[face_id] = person
            records.append({"Face": {"FaceId": face_id}})
        return {"FaceRecords": records}

    def detect_faces(self, Image, **_):
        people = people_in(Image["Bytes"])
        return {"FaceDetails": [{} for _ in people]}

    def search_faces_by_image(self, CollectionId, Image, **_):
        self._missing(CollectionId)
        people = people_in(Image["Bytes"])
        target = people[0] if people else None
        return {"FaceMatches": [
            {"Face": {"FaceId": face_id}, "Similarity": 99.5}
            for face_id, person in self.face_people.items() if person == target
        ]}
