import httpx

from app.core.config import OLLAMA_URL


async def get_models():
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{OLLAMA_URL}/api/tags")
        response.raise_for_status()
        return response.json()


async def generate_response(model: str, message: str):
    payload = {
        "model": model,
        "prompt": message,
        "stream": False,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{OLLAMA_URL}/api/generate",
            json=payload,
        )
        response.raise_for_status()
        return response.json()