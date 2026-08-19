import os
import httpx
import pytest


BASE_URL = os.environ.get("FORGE_API_URL", "http://127.0.0.1:8000")


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture(scope="session")
def client(base_url: str) -> httpx.Client:
    with httpx.Client(base_url=base_url, timeout=180.0) as c:
        yield c


@pytest.fixture(scope="session", autouse=True)
def _require_api_up(base_url: str):
    try:
        r = httpx.get(f"{base_url}/health", timeout=3.0)
        r.raise_for_status()
    except Exception as exc:
        pytest.skip(f"Forge API not reachable at {base_url}: {exc}")
