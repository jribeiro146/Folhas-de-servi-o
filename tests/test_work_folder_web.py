from types import SimpleNamespace

import pytest

from src.services.archive_service import ArchiveService
from src.services.editing_state_service import EditingStateService
from src.services.file_service import FileService
from src.services.graph_storage_service import GraphStorageError
from src.services.work_folder_service import (
    WorkFolderAmbiguousError,
    WorkFolderNotFoundError,
    WorkFolderUnsafeUrlError,
)
from src.web.application import create_app


SHAREPOINT_URL = (
    "https://sensorpointpt.sharepoint.com/sites/Tecnica/"
    "Shared%20Documents/07-Obras/Obras%20a%20realizar/0022%20-%20Base%20das%20Lages"
)


class StubWorkFolderService:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def resolve(self, number):
        self.calls.append(number)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.fixture
def app_factory(tmp_path, monkeypatch):
    import src.web.application as application_module

    monkeypatch.setattr(application_module, "ACTIVE_AUTH_PROVIDER", "none")
    monkeypatch.setattr(application_module, "AUTH_ENABLED", False)
    monkeypatch.setattr(application_module, "MAIL_ENABLED", False)
    monkeypatch.setattr(application_module, "TEAMS_NOTIFICATIONS_ENABLED", False)
    monkeypatch.setattr(application_module, "STORAGE_BACKEND", "local")

    def factory(work_folder_service, *, microsoft_auth_service=None):
        return create_app(
            file_service=FileService(tmp_path / "active"),
            archive_service=ArchiveService(),
            editing_state_service=EditingStateService(tmp_path / "editing"),
            work_folder_service=work_folder_service,
            microsoft_auth_service=microsoft_auth_service,
        )

    return factory


def test_work_folder_route_redirects_to_validated_sharepoint_url(app_factory):
    resolver = StubWorkFolderService(SimpleNamespace(web_url=SHAREPOINT_URL))
    client = app_factory(resolver).test_client()

    response = client.get("/work-folder/0022")

    assert response.status_code == 302
    assert response.headers["Location"] == SHAREPOINT_URL
    assert "no-store" in response.headers["Cache-Control"]
    assert resolver.calls == ["0022"]


@pytest.mark.parametrize("number", ["22", "00022", "00A2", "-022", "٠٠٢٢"])
def test_work_folder_route_rejects_non_four_digit_number(app_factory, number):
    resolver = StubWorkFolderService(SimpleNamespace(web_url=SHAREPOINT_URL))

    response = app_factory(resolver).test_client().get(f"/work-folder/{number}")

    assert response.status_code == 400
    assert "Número de obra inválido" in response.get_data(as_text=True)
    assert resolver.calls == []


@pytest.mark.parametrize(
    ("error", "status", "expected_text"),
    [
        (
            WorkFolderNotFoundError("missing"),
            404,
            "Pasta de obra não encontrada",
        ),
        (
            WorkFolderAmbiguousError("duplicate"),
            409,
            "Número de obra duplicado",
        ),
        (
            WorkFolderUnsafeUrlError("unsafe"),
            502,
            "Destino SharePoint inválido",
        ),
        (
            GraphStorageError("GRAPH_CLIENT_SECRET em falta"),
            502,
            "SharePoint temporariamente indisponível",
        ),
    ],
)
def test_work_folder_route_maps_resolution_errors(app_factory, error, status, expected_text):
    resolver = StubWorkFolderService(error)

    response = app_factory(resolver).test_client().get("/work-folder/0022")
    html = response.get_data(as_text=True)

    assert response.status_code == status
    assert expected_text in html
    assert str(error) not in html


def test_work_folder_route_is_protected_by_existing_login(app_factory, monkeypatch):
    import src.web.application as application_module

    monkeypatch.setattr(application_module, "ACTIVE_AUTH_PROVIDER", "microsoft")
    monkeypatch.setattr(application_module, "AUTH_ENABLED", True)
    resolver = StubWorkFolderService(SimpleNamespace(web_url=SHAREPOINT_URL))

    class LoggedOutMicrosoftAuth:
        @staticmethod
        def user_from_session(_payload):
            return None

    response = app_factory(
        resolver,
        microsoft_auth_service=LoggedOutMicrosoftAuth(),
    ).test_client().get("/work-folder/0022")

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login?next=")
    assert resolver.calls == []
