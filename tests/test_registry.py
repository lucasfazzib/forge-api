import asyncio
from unittest.mock import patch

import pytest

from app.tools.registry import (
    TOOLS,
    TOOL_REGISTRY,
    answer_schema_for,
    execute_tool,
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
