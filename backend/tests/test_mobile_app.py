"""The Android app loads the frontend from https://localhost and calls the API across origins."""

import pytest


def preflight(client, origin):
    return client.options(
        "/api/auth/login",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"},
    )


@pytest.mark.parametrize("origin", ["https://localhost", "capacitor://localhost"])
def test_app_origin_is_allowed(client, origin):
    r = preflight(client, origin)
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == origin


def test_other_origins_are_still_refused(client):
    r = preflight(client, "https://evil.example")
    assert r.status_code == 400
    assert "access-control-allow-origin" not in r.headers
