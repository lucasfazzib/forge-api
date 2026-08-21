import httpx

from fastapi import APIRouter, HTTPException

from app.agent import AgentRunner
from app.schemas.chat import AgentRequest, ChatRequest
from app.services.ollama import generate_response, get_models


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


@router.post("/agent")
async def agent(request: AgentRequest):
    try:
        state = await AgentRunner().run(request.model, request.message)
        return {
            "model": state.model,
            "used_tools": state.used_tools,
            "response": state.response,
        }
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Agent request failed: {exc}",
        )
