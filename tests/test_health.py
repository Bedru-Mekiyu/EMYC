import pytest
from httpx import AsyncClient
from app.core.config import Settings


@pytest.mark.asyncio
async def test_root_index(client: AsyncClient):
    """Verifies that GET / returns service status."""
    response = await client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    assert "EMYC" in data["project"]


@pytest.mark.asyncio
async def test_root_health_check(client: AsyncClient):
    """Verifies that the root /health endpoint responds with 200 OK for Render liveness probes."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "project" in data
    assert "environment" in data


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    """Verifies that the /api/v1/health endpoint responds with 200 and healthy status."""
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "project" in data


@pytest.mark.asyncio
async def test_readiness_check(client: AsyncClient):
    """Verifies that the readiness endpoint can connect to the database."""
    response = await client.get("/api/v1/health/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["database"] == "healthy"


def test_database_url_normalization():
    """Verifies that Settings automatically normalizes postgres:// and postgresql:// to postgresql+asyncpg://."""
    s1 = Settings(DATABASE_URL="postgres://user:pass@host:5432/db")
    assert s1.DATABASE_URL == "postgresql+asyncpg://user:pass@host:5432/db"

    s2 = Settings(DATABASE_URL="postgresql://user:pass@host:5432/db")
    assert s2.DATABASE_URL == "postgresql+asyncpg://user:pass@host:5432/db"

    s3 = Settings(DATABASE_URL="postgresql+asyncpg://user:pass@host:5432/db")
    assert s3.DATABASE_URL == "postgresql+asyncpg://user:pass@host:5432/db"


@pytest.mark.asyncio
async def test_bot_status_check(client: AsyncClient):
    """Verifies that /api/v1/health/bot-status provides diagnostic information."""
    response = await client.get("/api/v1/health/bot-status")
    assert response.status_code == 200
    data = response.json()
    assert "bot_mode" in data
    assert "active_competition" in data
    assert "total_registered_participants" in data


def test_webhook_url_normalization():
    """Verifies that Settings.get_normalized_webhook_url handles all format variations."""
    s1 = Settings(WEBHOOK_URL="https://emyc.onrender.com")
    assert s1.get_normalized_webhook_url() == "https://emyc.onrender.com/api/v1/telegram/webhook"

    s2 = Settings(WEBHOOK_URL="https://emyc.onrender.com/webhook")
    assert s2.get_normalized_webhook_url() == "https://emyc.onrender.com/api/v1/telegram/webhook"

    s3 = Settings(WEBHOOK_URL="https://emyc.onrender.com/api/v1/telegram/webhook")
    assert s3.get_normalized_webhook_url() == "https://emyc.onrender.com/api/v1/telegram/webhook"
