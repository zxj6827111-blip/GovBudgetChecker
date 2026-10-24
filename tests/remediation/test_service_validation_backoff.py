from io import BytesIO
from types import SimpleNamespace

from scripts.run_isolated_service_validation import request_with_backoff


def test_rate_rejected_upload_replays_original_file_bytes_and_respects_retry(monkeypatch):
    from scripts import run_isolated_service_validation as validation
    waits = []
    monkeypatch.setattr(validation.time, "sleep", waits.append)
    stream = BytesIO(b"original pdf bytes")
    bodies = []

    class Client:
        def request(self, method, url, **kwargs):
            bodies.append(kwargs["files"]["file"][1].read())
            return SimpleNamespace(status_code=429 if len(bodies) == 1 else 200,
                                   headers={"Retry-After": "60"})

    result = request_with_backoff(Client(), "POST", "/api/documents/upload",
                                  files={"file": ("original.pdf", stream, "application/pdf")})
    assert result.status_code == 200
    assert bodies == [b"original pdf bytes", b"original pdf bytes"]
    assert waits == [60]


def test_non_rate_failure_and_persistent_rejection_are_never_hidden(monkeypatch):
    from scripts import run_isolated_service_validation as validation
    waits = []
    monkeypatch.setattr(validation.time, "sleep", waits.append)
    calls = []

    class Client:
        def request(self, method, url, **kwargs):
            calls.append(url)
            return SimpleNamespace(status_code=self.status, headers={"Retry-After": "2"})

    client = Client()
    client.status = 500
    assert request_with_backoff(client, "POST", "/api/analyze/job").status_code == 500
    assert len(calls) == 1 and waits == []
    client.status = 429
    assert request_with_backoff(client, "POST", "/api/analyze/job").status_code == 429
    assert len(calls) == 5 and waits == [2, 2, 2]
