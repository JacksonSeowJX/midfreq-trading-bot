"""The app around the routes: CORS for the Vite dev server, and the built web app served at /."""
import json
from dataclasses import replace

import pytest

from midfreq.api.export_openapi import OUT
from midfreq.api.main import create_app


def test_cors_allows_the_vite_dev_server_only(client):
    ok = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    ok2 = client.get("/api/health", headers={"Origin": "http://127.0.0.1:5173"})
    assert ok2.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"
    other = client.get("/api/health", headers={"Origin": "https://example.com"})
    assert "access-control-allow-origin" not in other.headers


@pytest.fixture
def web(settings, tmp_path, client_for):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>midfreq</title>")
    (dist / "assets" / "app.js").write_text("console.log('hi')")
    (tmp_path / "secret.txt").write_text("secret")
    return client_for(create_app(replace(settings, web_dist=dist)))


def test_the_built_web_app_is_served_with_a_single_page_fallback(web):
    assert "<title>midfreq</title>" in web.get("/").text
    js = web.get("/assets/app.js")
    assert js.status_code == 200 and "console.log" in js.text
    assert "<title>midfreq</title>" in web.get("/live/sessions/abc").text


def test_the_web_app_never_shadows_the_api(web):
    assert web.get("/api/health").json()["status"] == "ok"
    missing = web.get("/api/no-such-route")
    assert missing.status_code == 404 and missing.json() == {"detail": "Not Found"}
    assert web.get("/api").status_code == 404


def test_the_web_app_serves_nothing_outside_dist(web):
    r = web.get("/..%2Fsecret.txt")
    assert "secret" not in r.text


def test_without_a_build_nothing_is_served_at_root(client):
    assert client.get("/").status_code == 404


def test_the_web_route_is_not_part_of_the_contract(settings, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("x")
    spec = create_app(replace(settings, web_dist=dist)).openapi()
    assert spec["paths"] == json.loads(OUT.read_text())["paths"]
