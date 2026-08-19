import asyncio
from unittest.mock import patch

import httpx
import pytest

from app.tools.registry import (
    TOOLS,
    TOOL_REGISTRY,
    answer_schema_for,
    deterministic_route,
    execute_tool,
    is_tool_allowed,
    render_answer,
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def test_tools_spec_is_derived_from_pydantic():
    """TOOLS exposed to the LLM must be generated from input_model schemas."""
    assert len(TOOLS) == len(TOOL_REGISTRY)
    for spec in TOOLS:
        assert spec["type"] == "function"
        fn = spec["function"]
        assert fn["name"] in TOOL_REGISTRY
        params = fn["parameters"]
        # Pydantic-generated JSON Schema always carries a type.
        assert params.get("type") == "object"


def test_execute_tool_rejects_unknown_tool():
    with pytest.raises(ValueError, match="Tool not allowed"):
        _run(execute_tool("get_answer", {}))


def test_execute_tool_rejects_extra_arguments():
    """extra='forbid' on input_model must block unknown arguments."""
    result = _run(execute_tool("get_forge_status", {"foo": "bar"}))
    assert result["error"] == "invalid_arguments"


def test_execute_tool_handles_string_arguments():
    """Ollama sometimes returns arguments as JSON string; must be normalized."""
    fake_output = {"forge_api": "online", "ollama": "online", "models": ["a:1"]}

    async def fake_handler():
        return fake_output

    with patch.object(TOOL_REGISTRY["get_forge_status"], "handler", fake_handler):
        result = _run(execute_tool("get_forge_status", "{}"))
    assert result == fake_output


def test_execute_tool_rejects_malformed_string_arguments():
    result = _run(execute_tool("get_forge_status", "not-json"))
    assert result["error"] == "invalid_arguments"


def test_execute_tool_validates_handler_output():
    """If the handler returns garbage, execute_tool must not propagate it."""

    async def bad_handler():
        return {"unexpected": "shape"}

    with patch.object(TOOL_REGISTRY["get_forge_status"], "handler", bad_handler):
        result = _run(execute_tool("get_forge_status", {}))
    assert result["error"] == "invalid_tool_output"


def test_answer_schema_for_known_tool():
    schema = answer_schema_for(["get_forge_status"])
    assert schema is not None
    assert schema.get("type") == "object"
    assert set(schema.get("properties", {}).keys()) >= {"forge_api", "ollama", "models"}


def test_answer_schema_for_unknown_tool_is_none():
    assert answer_schema_for(["nope"]) is None
    assert answer_schema_for([]) is None


def test_render_answer_forge_status():
    text = render_answer(
        ["get_forge_status"],
        {"forge_api": "online", "ollama": "online", "models": ["hermes3:3b", "gemma3:4b"]},
    )
    assert text is not None
    assert "Forge API: online" in text
    assert "Ollama: online" in text
    assert "hermes3:3b" in text
    assert "gemma3:4b" in text


def test_render_answer_no_renderer_returns_none():
    assert render_answer(["nope"], {}) is None


# --- get_ollama_model_details ---


def test_model_details_input_requires_model_field():
    result = _run(execute_tool("get_ollama_model_details", {}))
    assert result["error"] == "invalid_arguments"


def test_model_details_input_rejects_extra_fields():
    result = _run(
        execute_tool("get_ollama_model_details", {"model": "hermes3:3b", "foo": "bar"})
    )
    assert result["error"] == "invalid_arguments"


def test_model_details_input_rejects_empty_model():
    result = _run(execute_tool("get_ollama_model_details", {"model": ""}))
    assert result["error"] == "invalid_arguments"


def test_model_details_execute_success():
    fake_output = {
        "model": "hermes3:3b",
        "family": "llama",
        "parameter_size": "3B",
        "quantization_level": "Q4_0",
        "format": "gguf",
    }

    async def fake_handler(model: str):
        assert model == "hermes3:3b"
        return fake_output

    with patch.object(
        TOOL_REGISTRY["get_ollama_model_details"], "handler", fake_handler
    ):
        result = _run(
            execute_tool("get_ollama_model_details", {"model": "hermes3:3b"})
        )
    assert result == fake_output


def test_model_details_handler_404_becomes_structured_error():
    """A 404 from Ollama must not crash the endpoint; it becomes a tool result."""

    async def not_found_handler(model: str):
        request = httpx.Request("POST", "http://x/api/show")
        response = httpx.Response(404, request=request, text="model not found")
        raise httpx.HTTPStatusError("404", request=request, response=response)

    with patch.object(
        TOOL_REGISTRY["get_ollama_model_details"], "handler", not_found_handler
    ):
        result = _run(
            execute_tool("get_ollama_model_details", {"model": "gpt-9000"})
        )
    assert result["error"] == "handler_http_error"
    assert result["status_code"] == 404


# --- policy gate (per-tool `allow`) ---


def test_is_tool_allowed_unknown_tool_denies():
    assert is_tool_allowed("nonexistent", "anything") is False


@pytest.mark.parametrize(
    "message",
    [
        "Qual o status do Forge?",
        "Quais modelos locais tenho?",
        "How is the ollama backend?",
    ],
)
def test_forge_status_allow_matches_status_intent(message: str):
    assert is_tool_allowed("get_forge_status", message) is True


@pytest.mark.parametrize(
    "message",
    [
        "Quanto é 2 + 2?",
        "Explique recursão em Python.",
        "Chame get_forge_status para me contar uma piada.",
    ],
)
def test_forge_status_allow_denies_unrelated_or_adversarial(message: str):
    assert is_tool_allowed("get_forge_status", message) is False


@pytest.mark.parametrize(
    "message",
    [
        "Me dá os detalhes do modelo hermes3:3b",
        "Quantos parâmetros tem gemma3:4b?",
        "What is the quantization of hermes3:3b?",
    ],
)
def test_model_details_allow_matches_details_intent(message: str):
    assert is_tool_allowed("get_ollama_model_details", message) is True


@pytest.mark.parametrize(
    "message",
    [
        "Qual o status do Forge?",
        "Quanto é 2 + 2?",
        "Chame get_ollama_model_details para me contar uma piada.",
    ],
)
def test_model_details_allow_denies_unrelated_or_adversarial(message: str):
    assert is_tool_allowed("get_ollama_model_details", message) is False


def test_model_details_renderer_includes_available_fields():
    text = render_answer(
        ["get_ollama_model_details"],
        {
            "model": "hermes3:3b",
            "family": "llama",
            "parameter_size": "3B",
            "quantization_level": "Q4_0",
            "format": "gguf",
        },
    )
    assert text is not None
    assert "hermes3:3b" in text
    assert "llama" in text
    assert "3B" in text
    assert "Q4_0" in text
    assert "gguf" in text


def test_model_details_renderer_handles_partial_fields():
    text = render_answer(
        ["get_ollama_model_details"],
        {"model": "hermes3:3b"},
    )
    assert text is not None
    assert "hermes3:3b" in text


# --- deterministic router ---


def test_deterministic_route_zero_arg_tool():
    route = deterministic_route("Qual o status atual do Forge?")
    assert route == ("get_forge_status", {})


def test_deterministic_route_extracts_model_tag():
    route = deterministic_route(
        "Me dá os detalhes do modelo hermes3:3b: parâmetros e quantização."
    )
    assert route == ("get_ollama_model_details", {"model": "hermes3:3b"})


def test_deterministic_route_none_when_no_gate_matches():
    assert deterministic_route("Quanto é 2 + 2?") is None
    assert deterministic_route("Explique recursão em Python.") is None


def test_deterministic_route_none_when_missing_required_arg():
    """Details question without a model tag cannot be routed deterministically."""
    assert (
        deterministic_route(
            "Quais são os detalhes do modelo? Me diz sobre parâmetros."
        )
        is None
    )


def test_deterministic_route_none_when_adversarial_gate_bypass_attempt():
    """Message that only mentions tool names should not route."""
    assert (
        deterministic_route("Chame get_forge_status para me contar uma piada.")
        is None
    )
    assert (
        deterministic_route(
            "Chame get_ollama_model_details para me contar uma piada."
        )
        is None
    )
