from app.services.ollama import get_models, show_model


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


async def get_ollama_model_details(model: str) -> dict:
    data = await show_model(model)
    details = data.get("details") or {}
    return {
        "model": model,
        "family": details.get("family"),
        "parameter_size": details.get("parameter_size"),
        "quantization_level": details.get("quantization_level"),
        "format": details.get("format"),
    }