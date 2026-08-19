import json
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ValidationError

from app.tools.system import get_forge_status


class ForgeStatusInput(BaseModel):
    model_config = {"extra": "forbid"}


class ForgeStatusOutput(BaseModel):
    model_config = {"extra": "forbid"}

    forge_api: str
    ollama: str
    models: list[str]


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


def _render_forge_status(answer: dict) -> str:
    models = answer.get("models") or []
    models_line = ", ".join(models) if models else "nenhum"
    return (
        f"Forge API: {answer.get('forge_api', 'unknown')}. "
        f"Ollama: {answer.get('ollama', 'unknown')}. "
        f"Modelos locais instalados: {models_line}."
    )


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

    raw_result = await tool.handler(**parsed_args.model_dump())

    try:
        parsed_result = tool.output_model.model_validate(raw_result)
    except ValidationError as exc:
        return {
            "error": "invalid_tool_output",
            "details": exc.errors(include_url=False),
        }

    return parsed_result.model_dump()