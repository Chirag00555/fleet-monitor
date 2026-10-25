import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_auth_login_success(client: AsyncClient):
    response = await client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["operator"]["username"] == "admin"
    assert data["operator"]["role"] == "admin"

@pytest.mark.asyncio
async def test_auth_login_invalid_password(client: AsyncClient):
    response = await client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect username or password"

@pytest.mark.asyncio
async def test_auth_me_endpoint(client: AsyncClient):
    # 1. Login
    login_res = await client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert login_res.status_code == 200
    token = login_res.json()["access_token"]

    # 2. Call /api/auth/me with Bearer token
    me_res = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_res.status_code == 200
    data = me_res.json()
    assert data["username"] == "admin"
    assert data["is_active"] is True

@pytest.mark.asyncio
async def test_auth_me_unauthorized(client: AsyncClient):
    res = await client.get("/api/auth/me")
    assert res.status_code == 401
