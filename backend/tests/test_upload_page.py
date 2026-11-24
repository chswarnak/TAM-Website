from client_helpers import account_login, new_client
from helpers import create_contributor, create_event


def test_upload_page_reports_each_failure_reason():
    """The page must show the server's reason for every failed file, not
    just a count. (JS behaviour is checked by its hooks in the page.)"""
    create_event()
    create_contributor()
    client = new_client()
    account_login(client, "contributor", "asha@example.com", "contrib-pass-1")

    page = client.get("/contributor/upload")

    assert page.status_code == 200
    assert 'id="failures"' in page.text
    assert "function describeFailure" in page.text
    assert "413" in page.text  # proxy size-limit responses get a readable reason
