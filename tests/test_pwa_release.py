"""Real Flask asset boundary; conftest configures an isolated runtime before imports."""
import re
import pytest

@pytest.fixture
def web():
    from src.web.application import create_app
    app = create_app()
    return app, app.test_client()

def test_worker_is_bound_to_current_full_identity_and_never_http_cached(web):
    app, client = web
    result = client.get("/service-worker.js")
    identity = app.config["RELEASE_INFO"]["source_sha256"]
    assert result.status_code == 200
    assert result.mimetype == "application/javascript"
    assert f'const RELEASE_ID = "{identity}"' in result.text
    assert "__SENSORPOINT_RELEASE_SHA256__" not in result.text
    assert result.headers["X-App-Version"] == identity
    assert "no-store" in result.headers["Cache-Control"]
    assert "ETag" not in result.headers

def test_every_precached_public_asset_has_matching_identity_without_login(web):
    app, client = web
    worker = client.get("/service-worker.js").text
    paths = re.findall(r'"/(?:static/[^"]+|manifest.webmanifest)"', worker)
    assert len(set(paths)) >= 20
    for quoted in set(paths):
        result = client.get(quoted.strip('"') + "?v=" + app.config["ASSET_VERSION"])
        assert result.status_code == 200, quoted
        assert result.headers["X-App-Version"] == app.config["RELEASE_INFO"]["source_sha256"]

@pytest.mark.parametrize("path", ["/static/js/pwa.js", "/static/css/field-app.css", "/manifest.webmanifest"])
@pytest.mark.parametrize("query", ["v=old", "v=", "v=0000000000000000", "v={current}&v={current}"])
def test_server_never_serves_current_bytes_under_another_version(web, path, query):
    app, client = web
    result = client.get(path + "?" + query.format(current=app.config["ASSET_VERSION"]))
    assert result.status_code == 409
    assert "no-store" in result.headers["Cache-Control"]
    assert "addEventListener" not in result.text

@pytest.mark.parametrize("path", ["/", "/api/files", "/login", "/logout", "/nonexistent.pdf", "/work-folder/absent"])
def test_dynamic_and_private_responses_are_not_http_cached(web, path):
    _, client = web
    assert "no-store" in client.get(path).headers["Cache-Control"]

def test_unversioned_public_asset_remains_available(web):
    _, client = web
    assert client.get("/static/img/pwa/icon-192.png").status_code == 200

