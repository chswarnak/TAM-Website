import dataclasses
import logging

import pytest

import rekognition_service
from fakes import FakeRekognition, FakeS3, make_photo, make_selfie
from helpers import create_contributor, create_event, create_photo, db

RED, BLUE = (255, 0, 0), (0, 0, 255)


@pytest.fixture
def fakes(monkeypatch):
    s3 = FakeS3()
    rekognition = FakeRekognition(s3)
    monkeypatch.setattr(rekognition_service, "get_tam_rekognition_client",
                        lambda: rekognition)
    create_event()
    return s3, rekognition


def _stored_photo(s3, *people, authorized=True):
    contributor_id = create_contributor(email=f"c{len(s3.objects)}@example.com")
    key = f"tam-diwali-2026/contributor-001/{len(s3.objects)}.jpg"
    s3.objects[("tam-smart-event-photos", key)] = make_photo(*people)
    return create_photo(contributor_id, key, authorized=authorized)


def test_authorized_photo_is_indexed_and_found_by_selfie(fakes):
    s3, _ = fakes
    group = _stored_photo(s3, RED, BLUE)
    solo_blue = _stored_photo(s3, BLUE)

    assert rekognition_service.index_photo_now(group)["face_count"] == 2
    assert rekognition_service.index_photo_now(solo_blue)["face_count"] == 1

    red_matches = rekognition_service.find_matching_event_photos(make_selfie(RED))
    blue_matches = rekognition_service.find_matching_event_photos(make_selfie(BLUE))
    assert [m["photo_id"] for m in red_matches] == [group]
    assert [m["photo_id"] for m in blue_matches] == [group, solo_blue]


def test_unauthorized_photo_is_not_indexed(fakes):
    s3, _ = fakes
    photo = _stored_photo(s3, RED, authorized=False)
    with pytest.raises(PermissionError):
        rekognition_service.index_photo_now(photo)


def test_default_collection_is_never_created_implicitly(fakes):
    s3, rekognition = fakes
    rekognition.collections.clear()
    photo = _stored_photo(s3, RED)

    with pytest.raises(rekognition_service.FaceCollectionMissingError):
        rekognition_service.index_photo_now(photo)

    assert rekognition.created == []
    with db() as conn:
        job = conn.execute("SELECT status, error_message FROM photo_index_jobs").fetchone()
    assert job["status"] == "failed"
    assert "does not exist" in job["error_message"]


def test_explicit_collection_is_created_with_a_warning(fakes, monkeypatch, caplog):
    s3, rekognition = fakes
    monkeypatch.setattr(rekognition_service, "SETTINGS", dataclasses.replace(
        rekognition_service.SETTINGS,
        collection_id="local-test-faces", collection_id_explicit=True,
    ))
    photo = _stored_photo(s3, RED)

    with caplog.at_level(logging.WARNING, logger="rekognition_service"):
        result = rekognition_service.index_photo_now(photo)

    assert result["face_count"] == 1
    assert rekognition.created == ["local-test-faces"]
    assert any("CREATING" in r.message for r in caplog.records)


def test_failed_job_can_be_retried(fakes):
    s3, rekognition = fakes
    rekognition.collections.clear()
    photo = _stored_photo(s3, RED)
    with pytest.raises(rekognition_service.FaceCollectionMissingError):
        rekognition_service.index_photo_now(photo)

    rekognition.collections.add("tam-diwali-2026-faces")
    assert rekognition_service.index_photo_now(photo)["status"] == "completed"


def test_selfie_with_two_faces_is_rejected(fakes):
    with pytest.raises(ValueError):
        rekognition_service.find_matching_event_photos(make_photo(RED, BLUE))
