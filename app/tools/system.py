from app.services.ollama import get_models


async def get_forge_status() -> dict:
    models_data = await get_models()

    models = [
        model["name"]
        for model in models_data.get("models", [])
    ]

    return {
        "forge_api": "online",
        "ollama": "online",
        "models": models,
    }