import httpx

from fastapi import APIRouter, HTTPException

from app.schemas.chat import ChatRequest
from app.services.ollama import get_models, generate_response


router = APIRouter()


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "forge-api",
        "version": "0.1.0",
    }


@router.get("/models")
async def models():
    try:
        return await get_models()

    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Ollama request failed: {exc}",
        )


@router.post("/chat")
async def chat(request: ChatRequest):
    try:
        data = await generate_response(
            model=request.model,
            message=request.message,
        )

        return {
            "model": request.model,
            "response": data.get("response", ""),
        }

    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Ollama request failed: {exc}",
        )