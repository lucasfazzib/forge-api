import httpx
import json

from fastapi import APIRouter, HTTPException
from app.schemas.chat import ChatRequest
from app.services.ollama import get_models, generate_response
from app.schemas.chat import ChatRequest, AgentRequest
from app.services.ollama import (
    get_models,
    generate_response,
    chat_with_tools,
    chat_messages,
)
from app.tools.registry import TOOLS, execute_tool, answer_schema_for, render_answer

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

def forge_status_tool_allowed(message: str) -> bool:
    normalized = message.lower()

    # Strip literal tool names first so the user quoting the tool name
    # (adversarial or accidental) does not by itself satisfy the gate.
    for tool_name in ("get_forge_status",):
        normalized = normalized.replace(tool_name, " ")

    keywords = [
        "forge",
        "ollama",
        "modelo local",
        "modelos locais",
        "status",
        "models installed",
        "local models",
    ]

    return any(keyword in normalized for keyword in keywords)

@router.post("/agent")
async def agent(request: AgentRequest):
    try:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are the Forge AI assistant. "
                    "Tools are optional, not mandatory. "
                    "Use get_forge_status ONLY when the user asks about the current "
                    "Forge environment, Ollama status, Forge API status, or installed models. "
                    "For math, general knowledge, explanations, programming questions, "
                    "or anything unrelated to Forge runtime status, answer directly WITHOUT tools. "
                    "When a tool returns factual information, answer strictly from that result. "
                    "Never invent tool results."
                ),
            },
            {
                "role": "user",
                "content": request.message,
            },
        ]

        first_response = await chat_with_tools(
            model=request.model,
            messages=messages,
            tools=TOOLS,
        )

        assistant_message = first_response.get("message", {})
        tool_calls = assistant_message.get("tool_calls", [])

        if not tool_calls:
            content = assistant_message.get("content", "")

            if ("get_forge_status" in content and forge_status_tool_allowed(request.message)):
                tool_calls = [
                    {
                        "function": {
                            "name": "get_forge_status",
                            "arguments": {},
                        }
                    }
                ]

        if not tool_calls:
            # First response had no structured tool_call and no allowlisted fallback;
            # discard its content (may contain untrusted pseudo-tool-call text) and
            # ask the model for a direct answer without tools.
            direct_response = await chat_messages(
                model=request.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are answering the user directly in plain natural language. "
                            "You have NO tools available. Do not call, invoke, mention, "
                            "or describe any tool or function. "
                            "Do not output JSON, curly braces, or key/value pairs like "
                            "'name', 'arguments', 'function'. "
                            "Respond with a normal conversational sentence."
                        ),
                    },
                    {
                        "role": "user",
                        "content": request.message,
                    },
                ],
            )

            return {
                "model": request.model,
                "used_tools": [],
                "response": direct_response.get("message", {}).get(
                    "content",
                    "",
                ),
            }

        messages.append(assistant_message)

        used_tools: list[str] = []
        tool_results: list[dict] = []

        for tool_call in tool_calls:
            function = tool_call.get("function", {})
            tool_name = function.get("name")
            arguments = function.get("arguments", {})

            # Policy gate:
            # o LLM pode pedir a tool, mas nossa aplicação decide se autoriza.
            if (
                tool_name == "get_forge_status"
                and not forge_status_tool_allowed(request.message)
            ):
                continue

            result = await execute_tool(
                name=tool_name,
                arguments=arguments,
            )

            used_tools.append(tool_name)
            tool_results.append(result)

            messages.append(
                {
                    "role": "tool",
                    "tool_name": tool_name,
                    "content": json.dumps(result),
                }
            )

        if not used_tools:
            fallback_response = await chat_messages(
                model=request.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Answer the user's request directly. "
                            "Do not call or describe any tools or functions."
                        ),
                    },
                    {
                        "role": "user",
                        "content": request.message,
                    },
                ],
            )

            return {
                "model": request.model,
                "used_tools": [],
                "response": fallback_response.get("message", {}).get(
                    "content",
                    "",
                ),
            }

        # Prefer deterministic rendering from the validated tool result:
        # the app is authoritative over data, the LLM is authoritative over language.
        # Skip the final LLM call entirely when a renderer exists — no room for hallucination.
        first_result = tool_results[0] if tool_results else {}
        if "error" not in first_result:
            rendered_direct = render_answer(used_tools, first_result)
            if rendered_direct is not None:
                return {
                    "model": request.model,
                    "used_tools": used_tools,
                    "response": rendered_direct,
                }

        # Fallback: no renderer available → ask the LLM to synthesize,
        # constrained by the answer schema when the tool declares one.
        final_response = await chat_messages(
            model=request.model,
            messages=messages,
            format=answer_schema_for(used_tools),
        )

        final_content = final_response.get("message", {}).get("content", "")

        rendered: str | None = None
        if answer_schema_for(used_tools) is not None and final_content:
            try:
                structured = json.loads(final_content)
                rendered = render_answer(used_tools, structured)
            except json.JSONDecodeError:
                rendered = None

        return {
            "model": request.model,
            "used_tools": used_tools,
            "response": rendered if rendered is not None else final_content,
        }

    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Agent request failed: {exc}",
        )

