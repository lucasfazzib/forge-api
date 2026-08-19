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
from app.tools.registry import (
    TOOLS,
    TOOL_REGISTRY,
    execute_tool,
    is_tool_allowed,
    deterministic_route,
    answer_schema_for,
    render_answer,
)

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
    # Kept as a thin wrapper for backward compatibility with any external callers;
    # policy now lives in app.tools.registry.
    return is_tool_allowed("get_forge_status", message)

@router.post("/agent")
async def agent(request: AgentRequest):
    try:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are the Forge AI assistant. You have exactly two tools:\n"
                    "1) get_forge_status — call it when the user asks about Forge/Ollama "
                    "status, availability, or which local models are installed.\n"
                    "2) get_ollama_model_details — call it when the user asks about a "
                    "specific model's family, parameter size/count, quantization, or format. "
                    "The `model` argument must be the exact model tag mentioned by the user "
                    "(e.g. 'hermes3:3b', 'gemma3:4b').\n"
                    "You MUST call the appropriate tool instead of guessing model information. "
                    "You do NOT have prior knowledge about any specific Ollama model's parameters, "
                    "family or quantization — that data only comes from get_ollama_model_details. "
                    "If you do not know the exact model tag, ask the user; do not guess. "
                    "For math, general knowledge, explanations, programming questions, "
                    "or anything unrelated to Forge runtime, answer directly WITHOUT tools. "
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
            # Deterministic router: if the LLM failed to emit a tool_call but
            # exactly one tool's allow gate matches the user message and we
            # can supply its arguments, invoke it explicitly. This is the
            # trust boundary that says: the app is authoritative over routing
            # when the model is unreliable (small models with weak tool
            # selection benefit the most).
            route = deterministic_route(request.message)
            if route is not None:
                routed_name, routed_args = route
                tool_calls = [
                    {
                        "function": {
                            "name": routed_name,
                            "arguments": routed_args,
                        }
                    }
                ]

        if not tool_calls:
            content = assistant_message.get("content", "")

            # Textual fallback: only for zero-argument tools whose name the model
            # leaked into free-form content AND whose policy gate allows this
            # message. Anything else is treated as untrusted text.
            for tool_name, tool in TOOL_REGISTRY.items():
                required = tool.input_model.model_json_schema().get("required", [])
                if required:
                    continue
                if tool_name in content and is_tool_allowed(tool_name, request.message):
                    tool_calls = [
                        {
                            "function": {
                                "name": tool_name,
                                "arguments": {},
                            }
                        }
                    ]
                    break

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

            # Policy gate is per-tool and lives in the registry.
            # The LLM may propose a tool; the app decides whether to authorize it.
            if not is_tool_allowed(tool_name, request.message):
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
        # If any tool returned an error, skip the schema so the LLM can
        # respond in free-form (e.g. "model not found").
        any_error = any("error" in r for r in tool_results)
        fmt = None if any_error else answer_schema_for(used_tools)

        final_response = await chat_messages(
            model=request.model,
            messages=messages,
            format=fmt,
        )

        final_content = final_response.get("message", {}).get("content", "")

        rendered: str | None = None
        if fmt is not None and final_content:
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

