from __future__ import annotations

from peawork import __version__


def test_health_endpoints(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["deployment"] == "test"
    assert body["database"] == "sqlite"

    assert client.get("/health/live").json()["status"] == "ok"

    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["organization"] == "PeaWork Test Organisation"


def test_security_headers_are_present(client):
    response = client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-request-id"]


def test_static_assets_and_manifest(client):
    css = client.get("/static/app.css")
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")

    htmx = client.get("/static/htmx.min.js")
    assert htmx.status_code == 200
    assert len(htmx.content) > 10_000

    manifest = client.get("/static/manifest.webmanifest")
    assert manifest.status_code == 200


def test_metrics_endpoint_is_exposed_when_no_token_is_configured(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "peawork_request" in response.text or "http_request" in response.text or "python_info" in response.text
