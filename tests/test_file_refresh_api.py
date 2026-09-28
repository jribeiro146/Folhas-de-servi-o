"""The HTTP reply must retain the identity of the refresh actually requested."""
import pytest

from src.services.file_mutex import FileMutexBusy
from src.services.editing_state_service import EditingStateService
from src.services.file_service import FileService
from src.web.application import create_app


class RacingRefresh:
    def __init__(self):
        self.requests = []

    def request_refresh(self, *, force=False):
        self.requests.append(force)
        return {"refresh_id": "requested-run", "in_progress": True,
                "refresh_sequence": 1, "generation_id": "generation-one"}

    def status(self):
        # A newer request can arrive between scheduling and serializing the API reply.
        return {"refresh_id": "newer-run", "in_progress": True}


def test_files_reply_preserves_requested_refresh_even_when_shared_status_changes(tmp_path):
    refresh = RacingRefresh()
    app = create_app(file_service=FileService(tmp_path / "active"),
        editing_state_service=EditingStateService(tmp_path / "editing"), graph_refresh_coordinator=refresh)
    response = app.test_client().get("/api/files?refresh=1")
    assert response.status_code == 200
    result = response.get_json()
    assert result["requested_refresh_id"] == "requested-run"
    assert result["requested_refresh_sequence"] == 1
    assert result["requested_refresh_generation"] == "generation-one"
    assert result["refresh"]["refresh_id"] == "newer-run"
    assert refresh.requests == [True]


def test_files_snapshot_does_not_start_another_refresh(tmp_path):
    refresh = RacingRefresh()
    app = create_app(file_service=FileService(tmp_path / "active"),
        editing_state_service=EditingStateService(tmp_path / "editing"), graph_refresh_coordinator=refresh)
    result = app.test_client().get("/api/files?refresh=0").get_json()
    assert result["requested_refresh_id"] is None
    assert refresh.requests == []


@pytest.mark.parametrize("url", ["/api/files?refresh=1", "/api/files?refresh=0", "/"])
def test_busy_refresh_returns_retryable_response_instead_of_500(tmp_path, url):
    class BusyRefresh:
        def request_refresh(self, **kwargs):
            raise FileMutexBusy()

        def status(self):
            raise FileMutexBusy()

    app = create_app(file_service=FileService(tmp_path / "active"),
        editing_state_service=EditingStateService(tmp_path / "editing"),
        graph_refresh_coordinator=BusyRefresh())
    response = app.test_client().get(url)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "2"
    assert response.get_json()["code"] == "temporarily_busy"
