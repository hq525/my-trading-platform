def test_health_is_open(client):
    import httpx

    fresh = httpx.Client(transport=httpx.ASGITransport(app=client.app),
                         base_url="http://test")
    assert fresh.get("/api/health").status_code == 200


def test_protected_route_requires_login(client):
    import httpx

    fresh = httpx.Client(transport=httpx.ASGITransport(app=client.app),
                         base_url="http://test")
    assert fresh.get("/api/accounts").status_code == 401


def test_wrong_password_rejected(client):
    assert client.post("/api/login", json={"password": "nope"}).status_code == 401


def test_login_then_access(client):
    # the fixture already logged in
    assert client.get("/api/accounts").status_code == 200
