from io import BytesIO
from urllib import error

import pytest

from src.services.graph_storage_service import (
    GRAPH_ROOT,
    GraphConfig,
    GraphStorageError,
    GraphStorageService,
)
from src.services.work_folder_service import (
    WorkFolderAmbiguousError,
    WorkFolderInvalidNumberError,
    WorkFolderNotFoundError,
    WorkFolderService,
    WorkFolderUnsafeUrlError,
)


SHAREPOINT_HOST = "sensorpointpt.sharepoint.com"


def folder_item(name, web_url=None):
    return {
        "name": name,
        "webUrl": (
            web_url if web_url is not None else f"https://{SHAREPOINT_HOST}/sites/Tecnica/{name}"
        ),
        "folder": {"childCount": 0},
    }


class SequencedGraph:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def list_folder_children(self, folder_path):
        self.calls.append(folder_path)
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


def build_service(graph, *, clock=lambda: 0.0, cache_seconds=300):
    return WorkFolderService(
        graph,
        folder_path="07-Obras/Obras a realizar",
        allowed_hostname=SHAREPOINT_HOST,
        cache_seconds=cache_seconds,
        clock=clock,
    )


def test_resolver_matches_only_direct_folders_with_exact_prefix():
    graph = SequencedGraph([[
        folder_item("0022 - Base das Lages"),
        folder_item("00220 - Número demasiado longo"),
        folder_item("0022 Sem separador"),
        folder_item("22 - Sem zeros"),
        {
            "name": "0022 - Ficheiro, não pasta",
            "webUrl": f"https://{SHAREPOINT_HOST}/file",
            "file": {},
        },
    ]])

    resolved = build_service(graph).resolve("0022")

    assert resolved.name == "0022 - Base das Lages"
    assert resolved.number == "0022"
    assert graph.calls == ["07-Obras/Obras a realizar"]


def test_resolver_rejects_duplicate_work_numbers():
    graph = SequencedGraph([[
        folder_item("0022 - Primeira"),
        folder_item("0022- Segunda"),
    ]])

    with pytest.raises(WorkFolderAmbiguousError):
        build_service(graph).resolve("0022")


def test_resolver_refreshes_valid_cache_once_before_not_found():
    graph = SequencedGraph([
        [folder_item("0036 - Existente")],
        [folder_item("0036 - Existente"), folder_item("0022 - Criada agora")],
    ])
    service = build_service(graph)

    assert service.resolve("0036").number == "0036"
    assert service.resolve("0022").name == "0022 - Criada agora"
    assert len(graph.calls) == 2


def test_resolver_returns_not_found_after_forced_refresh():
    graph = SequencedGraph([
        [folder_item("0036 - Existente")],
        [folder_item("0036 - Existente")],
    ])
    service = build_service(graph)
    service.resolve("0036")

    with pytest.raises(WorkFolderNotFoundError):
        service.resolve("0022")

    assert len(graph.calls) == 2


def test_cache_is_reused_and_refreshed_after_expiration():
    now = [0.0]
    graph = SequencedGraph([
        [folder_item("0022 - Inicial")],
        [folder_item("0022 - Renomeada")],
    ])
    service = build_service(graph, clock=lambda: now[0])

    assert service.resolve("0022").name == "0022 - Inicial"
    assert service.resolve("0022").name == "0022 - Inicial"
    assert len(graph.calls) == 1

    now[0] = 301.0
    assert service.resolve("0022").name == "0022 - Renomeada"
    assert len(graph.calls) == 2


@pytest.mark.parametrize(
    "web_url",
    [
        "http://sensorpointpt.sharepoint.com/sites/Tecnica/0022",
        "https://evil.example/sites/Tecnica/0022",
        "https://sensorpointpt.sharepoint.com.evil.example/0022",
        "https://user@sensorpointpt.sharepoint.com/0022",
        "",
    ],
)
def test_resolver_rejects_unsafe_sharepoint_urls(web_url):
    graph = SequencedGraph([[folder_item("0022 - Obra", web_url=web_url)]])

    with pytest.raises(WorkFolderUnsafeUrlError):
        build_service(graph).resolve("0022")


@pytest.mark.parametrize("number", [None, "", "22A", "12345", -1])
def test_resolver_rejects_invalid_numbers(number):
    with pytest.raises(WorkFolderInvalidNumberError):
        build_service(SequencedGraph([[]])).resolve(number)


def test_graph_folder_listing_follows_every_odata_page(monkeypatch):
    service = GraphStorageService(GraphConfig("tenant", "client", "secret", "drive"))
    page_two_url = f"{GRAPH_ROOT}/drives/drive/items/works/children?$skiptoken=two"
    page_three_url = f"{GRAPH_ROOT}/drives/drive/items/works/children?$skiptoken=three"
    pages = {
        "first": {
            "value": [{"id": f"a-{index}"} for index in range(200)],
            "@odata.nextLink": page_two_url,
        },
        page_two_url: {
            "value": [{"id": f"b-{index}"} for index in range(200)],
            "@odata.nextLink": page_three_url,
        },
        page_three_url: {
            "value": [{"id": f"c-{index}"} for index in range(89)],
        },
    }
    requested_urls = []

    monkeypatch.setattr(
        service,
        "_graph_json",
        lambda method, path: {"id": "works", "name": "Obras a realizar", "folder": {}},
    )

    def fake_request_json(url, **kwargs):
        requested_urls.append(url)
        return pages["first"] if len(requested_urls) == 1 else pages[url]

    monkeypatch.setattr(service, "_request_json", fake_request_json)

    children = service.list_folder_children("07-Obras/Obras a realizar")

    assert len(children) == 489
    assert "$top=200" in requested_urls[0]
    assert requested_urls[1:] == [page_two_url, page_three_url]


def test_graph_listing_rejects_external_next_link(monkeypatch):
    service = GraphStorageService(GraphConfig("tenant", "client", "secret", "drive"))
    monkeypatch.setattr(
        service,
        "_graph_json",
        lambda method, path: {"id": "works", "folder": {}},
    )
    monkeypatch.setattr(
        service,
        "_request_json",
        lambda url, **kwargs: {"value": [], "@odata.nextLink": "https://evil.example/page"},
    )

    with pytest.raises(GraphStorageError, match="paginação inválida"):
        service.list_folder_children("07-Obras/Obras a realizar")


def test_graph_get_retries_once_on_throttling(monkeypatch):
    import src.services.graph_storage_service as graph_module

    service = GraphStorageService(GraphConfig("tenant", "client", "secret", "drive"))
    calls = []

    def fake_urlopen(req, timeout):
        calls.append(req)
        if len(calls) == 1:
            raise error.HTTPError(
                req.full_url,
                429,
                "throttled",
                {"Retry-After": "0"},
                BytesIO(b"busy"),
            )
        return BytesIO(b"ok")

    monkeypatch.setattr(graph_module.request, "urlopen", fake_urlopen)

    assert service._request_bytes(
        f"{GRAPH_ROOT}/drives/drive",
        method="GET",
        authenticated=False,
    ) == b"ok"
    assert len(calls) == 2


def test_graph_post_is_not_retried(monkeypatch):
    import src.services.graph_storage_service as graph_module

    service = GraphStorageService(GraphConfig("tenant", "client", "secret", "drive"))
    calls = []

    def fake_urlopen(req, timeout):
        calls.append(req)
        raise error.HTTPError(req.full_url, 503, "busy", {}, BytesIO(b"busy"))

    monkeypatch.setattr(graph_module.request, "urlopen", fake_urlopen)

    with pytest.raises(GraphStorageError, match="HTTP 503"):
        service._request_bytes(
            f"{GRAPH_ROOT}/drives/drive",
            method="POST",
            data=b"{}",
            authenticated=False,
        )

    assert len(calls) == 1
