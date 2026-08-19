import json
import re
from typing import Any, Awaitable, Callable

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.tools.system import get_forge_status, get_ollama_model_details


class ForgeStatusInput(BaseModel):
    model_config = {"extra": "forbid"}


class ForgeStatusOutput(BaseModel):
    model_config = {"extra": "forbid"}

    forge_api: str
    ollama: str
    models: list[str]


class ModelDetailsInput(BaseModel):
    model_config = {"extra": "forbid"}

    model: str = Field(min_length=1)


class ModelDetailsOutput(BaseModel):
    model_config = {"extra": "forbid"}

    model: str
    family: str | None = None
    parameter_size: str | None = None
    quantization_level: str | None = None
    format: str | None = None


class RegisteredTool(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: Callable[..., Awaitable[dict]]
    # answer_model constrains the final LLM answer when this tool ran.
    answer_model: type[BaseModel] | None = None
    # answer_renderer turns the structured answer into user-facing text.
    answer_renderer: Callable[[dict], str] | None = None
    # allow decides, from the raw user message, whether this tool may run
    # in this turn. Returning False triggers the policy gate.
    allow: Callable[[str], bool] | None = None
    # arg_extractor pulls this tool's arguments straight from the user message.
    # Used by the deterministic router when the LLM fails to emit a tool_call.
    arg_extractor: Callable[[str], dict | None] | None = None


def _strip_tool_names(message: str, names: list[str]) -> str:
    normalized = message.lower()
    for name in names:
        normalized = normalized.replace(name, " ")
    return normalized


def _strip_all_tool_names(message: str) -> str:
    return _strip_tool_names(message, list(TOOL_REGISTRY.keys()))


def _forge_status_allow(message: str) -> bool:
    normalized = _strip_all_tool_names(message)
    keywords = [
        "forge",
        "ollama",
        "modelo local",
        "modelos locais",
        "status",
        "models installed",
        "local models",
    ]
    return any(k in normalized for k in keywords)


def _model_details_allow(message: str) -> bool:
    normalized = _strip_all_tool_names(message)
    keywords = [
        "detalhes do modelo",
        "detalhes de modelo",
        "informações do modelo",
        "informacoes do modelo",
        "parâmetros",
        "parametros",
        "quantização",
        "quantizacao",
        "quantization",
        "model details",
        "model info",
        "family",
        "família do modelo",
        "familia do modelo",
    ]
    return any(k in normalized for k in keywords)


def _render_forge_status(answer: dict) -> str:
    models = answer.get("models") or []
    models_line = ", ".join(models) if models else "nenhum"
    return (
        f"Forge API: {answer.get('forge_api', 'unknown')}. "
        f"Ollama: {answer.get('ollama', 'unknown')}. "
        f"Modelos locais instalados: {models_line}."
    )


def _render_model_details(answer: dict) -> str:
    parts = [f"Modelo: {answer.get('model', 'unknown')}."]
    if answer.get("family"):
        parts.append(f"Família: {answer['family']}.")
    if answer.get("parameter_size"):
        parts.append(f"Parâmetros: {answer['parameter_size']}.")
    if answer.get("quantization_level"):
        parts.append(f"Quantização: {answer['quantization_level']}.")
    if answer.get("format"):
        parts.append(f"Formato: {answer['format']}.")
    return " ".join(parts)


_MODEL_TAG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]*:[A-Za-z0-9][A-Za-z0-9_.\-]*")


def _extract_model_details_args(message: str) -> dict | None:
    match = _MODEL_TAG_RE.search(message)
    if match is None:
        return None
    return {"model": match.group(0)}


TOOL_REGISTRY: dict[str, RegisteredTool] = {
    "get_forge_status": RegisteredTool(
        name="get_forge_status",
        description=(
            "Get the CURRENT operational status of the Forge AI lab. "
            "Use ONLY when the user explicitly asks about Forge status, "
            "Ollama availability, Forge API availability, or which local "
            "models are currently installed. "
            "Never use this tool for math, general knowledge, explanations, "
            "coding questions, or unrelated requests."
        ),
        input_model=ForgeStatusInput,
        output_model=ForgeStatusOutput,
        handler=get_forge_status,
        answer_model=ForgeStatusOutput,
        answer_renderer=_render_forge_status,
        allow=_forge_status_allow,
    ),
    "get_ollama_model_details": RegisteredTool(
        name="get_ollama_model_details",
        description=(
            "Get detailed information about ONE specific local Ollama model "
            "(family, parameter size, quantization level, format). "
            "Use ONLY when the user explicitly asks about a specific model's "
            "details, parameters, quantization or family. "
            "Never use for general questions or when the model name is not clear."
        ),
        input_model=ModelDetailsInput,
        output_model=ModelDetailsOutput,
        handler=get_ollama_model_details,
        answer_model=ModelDetailsOutput,
        answer_renderer=_render_model_details,
        allow=_model_details_allow,
        arg_extractor=_extract_model_details_args,
    ),
}


def _tool_spec(tool: RegisteredTool) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_model.model_json_schema(),
        },
    }


TOOLS: list[dict] = [_tool_spec(tool) for tool in TOOL_REGISTRY.values()]


def is_tool_allowed(name: str, message: str) -> bool:
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        return False
    if tool.allow is None:
        return True
    return tool.allow(message)


def deterministic_route(message: str) -> tuple[str, dict] | None:
    """App-side router for when the LLM fails to emit a valid tool_call.

    Returns (tool_name, arguments) only when exactly one tool's allow gate
    matches the user message AND we can supply required arguments — either
    because the tool takes none or because its arg_extractor could pull them
    from the message. Otherwise returns None and the caller should either
    fall back to a free-form answer or trust the LLM.
    """
    candidates = [name for name in TOOL_REGISTRY if is_tool_allowed(name, message)]
    if len(candidates) != 1:
        return None

    tool_name = candidates[0]
    tool = TOOL_REGISTRY[tool_name]
    required = tool.input_model.model_json_schema().get("required", [])

    if not required:
        return (tool_name, {})

    if tool.arg_extractor is None:
        return None

    args = tool.arg_extractor(message)
    if not args:
        return None

    return (tool_name, args)


def answer_schema_for(tool_names: list[str]) -> dict | None:
    for name in tool_names:
        tool = TOOL_REGISTRY.get(name)
        if tool and tool.answer_model is not None:
            return tool.answer_model.model_json_schema()
    return None


def render_answer(tool_names: list[str], structured: dict) -> str | None:
    for name in tool_names:
        tool = TOOL_REGISTRY.get(name)
        if tool and tool.answer_renderer is not None:
            return tool.answer_renderer(structured)
    return None


async def execute_tool(name: str, arguments: Any) -> dict:
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        raise ValueError(f"Tool not allowed: {name}")

    # Ollama may return arguments as dict or as a JSON string; normalize.
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            return {
                "error": "invalid_arguments",
                "reason": "arguments is not valid JSON",
            }

    if arguments is None:
        arguments = {}

    try:
        parsed_args = tool.input_model.model_validate(arguments)
    except ValidationError as exc:
        return {
            "error": "invalid_arguments",
            "details": exc.errors(include_url=False),
        }

    try:
        raw_result = await tool.handler(**parsed_args.model_dump())
    except httpx.HTTPStatusError as exc:
        return {
            "error": "handler_http_error",
            "status_code": exc.response.status_code,
            "reason": exc.response.text[:200],
        }
    except httpx.HTTPError as exc:
        return {"error": "handler_transport_error", "reason": str(exc)}

    try:
        parsed_result = tool.output_model.model_validate(raw_result)
    except ValidationError as exc:
        return {
            "error": "invalid_tool_output",
            "details": exc.errors(include_url=False),
        }

    return parsed_result.model_dump()