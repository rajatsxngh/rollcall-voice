import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path):
    # _env_file=None: tests never read the real .env or use real keys.
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        nvidia_api_keys="",
        nvidia_api_key="",
        openrouter_api_key="",
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client
