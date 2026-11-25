import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "index.html").write_text("<html>spa</html>")
    (tmp_path / "secret.txt").write_text("outside")
    monkeypatch.setenv("LF_WEB_DIR", str(tmp_path / "web"))
    sys.modules.pop("app.main", None)
    app = importlib.import_module("app.main").app
    yield TestClient(app)
    sys.modules.pop("app.main", None)


def test_traversal_does_not_leave_the_web_dir(client: TestClient) -> None:
    r = client.get("/..%2fsecret.txt")
    assert "outside" not in r.text


def test_post_to_unknown_path_is_not_the_spa(client: TestClient) -> None:
    assert client.post("/labs/x").status_code == 405


def test_healthz_prefix_lookalike_still_gets_the_spa(client: TestClient) -> None:
    assert "spa" in client.get("/healthzzz").text


def test_null_byte_path_is_a_404_not_a_500(client: TestClient) -> None:
    assert client.get("/%00").status_code == 404


def test_index_is_not_cached_and_head_works(client: TestClient) -> None:
    assert client.get("/").headers["cache-control"] == "no-cache"
    assert client.get("/labs/x").headers["cache-control"] == "no-cache"
    assert client.head("/").status_code == 200


def test_missing_asset_or_file_is_404_not_html(client: TestClient) -> None:
    assert client.get("/assets/old-123.js").status_code == 404
    assert client.get("/favicon.ico").status_code == 404


def test_api_docs_are_off_by_default(client: TestClient) -> None:
    assert "swagger" not in client.get("/docs").text.lower()
    assert client.get("/openapi.json").status_code == 404


def test_head_on_health_and_index_html_no_cache(client: TestClient) -> None:
    with client:  # lifespan sets the state /api/health reads
        assert client.head("/healthz").status_code == 200
        assert client.head("/api/health").status_code == 200
    r = client.get("/index.html")
    assert "spa" in r.text and r.headers["cache-control"] == "no-cache"


def test_dotted_deep_link_gets_the_spa_but_unknown_static_ext_is_404(client: TestClient) -> None:
    assert "spa" in client.get("/labs/linux-1.2").text
    assert client.get("/logo.PNG").status_code == 404
    assert client.get("/assets/b.woff2").status_code == 404


def test_terminal_route_is_401_not_the_spa(client: TestClient) -> None:
    r = client.get("/s/00000000-0000-0000-0000-000000000000/term")
    assert r.status_code == 401 and "spa" not in r.text


def test_index_cache_header_follows_the_resolved_path(client: TestClient) -> None:
    r = client.get("/foo/../index.html")
    assert "spa" in r.text
    assert r.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize(
    "path", ["/labs/node.js", "/labs/x/tasks/config.json", "/labs/a.b/tasks/1.1.css"]
)
def test_deep_links_with_a_static_extension_get_the_spa(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 200
    assert "spa" in r.text


@pytest.mark.parametrize("path", ["/app.js", "/favicon.ico", "/assets/m.js", "/assets/x/y.css"])
def test_missing_top_level_and_asset_files_are_404(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 404
