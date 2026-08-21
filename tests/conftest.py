import os
import httpx
import pytest


BASE_URL = os.environ.get("FORGE_API_URL", "http://127.0.0.1:8000")


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture(scope="session")
def client(base_url: str) -> httpx.Client:
    # Health check lives here — not in an autouse session fixture — so it only
    # affects tests that actually request an HTTP client (integration tests).
    # Unit tests never touch the network.
    try:
        r = httpx.get(f"{base_url}/health", timeout=3.0)
        r.raise_for_status()
    except Exception as exc:
        pytest.skip(f"Forge API not reachable at {base_url}: {exc}")

    with httpx.Client(base_url=base_url, timeout=180.0) as c:
        yield c
