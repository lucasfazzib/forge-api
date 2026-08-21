import asyncio

from unittest.mock import AsyncMock

import pytest

from app.agent.runner import AgentRunner
from app.agent.state import AgentState


def _run(coro):
    return asyncio.run(coro)


def _first_llm_reply(*, content: str = "", tool_calls: list[dict] | None = None) -> dict:
    message: dict = {"content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"message": message}


def _final_llm_reply(content: str) -> dict:
    return {"message": {"content": content}}


def _make_runner(
    *,
    first_reply: dict | None = None,
    final_reply: dict | None = None,
    tool_result: dict | None = None,
) -> tuple[AgentRunner, AsyncMock, AsyncMock, AsyncMock]:
    chat_with_tools = AsyncMock(return_value=first_reply or _first_llm_reply())
    chat_messages = AsyncMock(return_value=final_reply or _final_llm_reply(""))
    execute_tool = AsyncMock(return_value=tool_result or {})
    runner = AgentRunner(
        chat_with_tools_fn=chat_with_tools,
        chat_messages_fn=chat_messages,
        execute_tool_fn=execute_tool,
    )
    return runner, chat_with_tools, chat_messages, execute_tool


# --- No tool_call, no router, no textual fallback → direct answer path ---


def test_run_no_tool_call_returns_direct_answer_and_does_not_execute_tools():
    runner, _, chat_messages, execute_tool = _make_runner(
        first_reply=_first_llm_reply(content="just chatting"),
        final_reply=_final_llm_reply("2 plus 2 é 4."),
    )

    state = _run(runner.run("hermes3:3b", "Quanto é 2 + 2?"))

    assert isinstance(state, AgentState)
    assert state.used_tools == []
    assert state.tool_results == []
    assert state.response == "2 plus 2 é 4."
    execute_tool.assert_not_called()
    # The direct-answer call must not carry tools (format=None omitted).
    call_kwargs = chat_messages.call_args.kwargs
    assert "format" not in call_kwargs or call_kwargs.get("format") is None


# --- LLM emits structured tool_call, policy gate allows, renderer available ---


def test_run_structured_tool_call_renders_deterministically_and_skips_final_llm():
    tool_result = {
        "forge_api": "online",
        "ollama": "online",
        "models": ["hermes3:3b", "gemma3:4b"],
    }
    tool_calls = [{"function": {"name": "get_forge_status", "arguments": {}}}]
    runner, _, chat_messages, execute_tool = _make_runner(
        first_reply=_first_llm_reply(tool_calls=tool_calls),
        tool_result=tool_result,
    )

    state = _run(
        runner.run("hermes3:3b", "Qual é o status atual do Forge?")
    )

    assert state.used_tools == ["get_forge_status"]
    assert state.tool_results == [tool_result]
    assert "Forge API: online" in state.response
    assert "hermes3:3b" in state.response
    # Renderer path: no final synthesis call to the LLM.
    chat_messages.assert_not_called()
    execute_tool.assert_awaited_once_with("get_forge_status", {})


# --- LLM proposes a tool but the policy gate denies for this user intent ---


def test_run_tool_call_denied_by_policy_gate_falls_back_to_direct_answer():
    tool_calls = [{"function": {"name": "get_forge_status", "arguments": {}}}]
    runner, _, chat_messages, execute_tool = _make_runner(
        first_reply=_first_llm_reply(tool_calls=tool_calls),
        final_reply=_final_llm_reply("piada aqui"),
    )

    # This user message does not match the forge_status gate.
    state = _run(runner.run("hermes3:3b", "Me conte uma piada."))

    assert state.used_tools == []
    assert state.tool_results == []
    execute_tool.assert_not_called()
    assert state.response == "piada aqui"


# --- Deterministic router: LLM did not emit tool_call, but message triggers routing ---


def test_run_deterministic_router_injects_tool_call():
    tool_result = {
        "forge_api": "online",
        "ollama": "online",
        "models": ["hermes3:3b"],
    }
    runner, _, chat_messages, execute_tool = _make_runner(
        first_reply=_first_llm_reply(content="not a tool call"),
        tool_result=tool_result,
    )

    state = _run(
        runner.run("hermes3:3b", "Qual o status do Forge agora?")
    )

    assert state.used_tools == ["get_forge_status"]
    chat_messages.assert_not_called()
    execute_tool.assert_awaited_once_with("get_forge_status", {})


# --- Textual fallback: LLM leaks tool name into content, gate allows ---


def test_run_textual_fallback_triggers_zero_arg_tool():
    tool_result = {
        "forge_api": "online",
        "ollama": "online",
        "models": ["hermes3:3b"],
    }
    runner, _, chat_messages, execute_tool = _make_runner(
        # Content contains the tool name AND the user asks a status question,
        # but the LLM did NOT emit a structured tool_call. The user message must
        # also satisfy the policy gate. Craft a message that triggers routing
        # but is also compatible with textual fallback.
        first_reply=_first_llm_reply(
            content='I would call get_forge_status here'
        ),
        tool_result=tool_result,
    )

    state = _run(runner.run("hermes3:3b", "Status do Forge?"))

    assert state.used_tools == ["get_forge_status"]
    execute_tool.assert_awaited_once_with("get_forge_status", {})


# --- Tool result contains error → final LLM synthesis without format ---


def test_run_tool_error_result_calls_final_llm_without_format():
    tool_calls = [
        {
            "function": {
                "name": "get_ollama_model_details",
                "arguments": {"model": "gpt-9000:xl"},
            }
        }
    ]
    tool_result = {"error": "handler_http_error", "status_code": 404, "reason": "..."}
    runner, _, chat_messages, execute_tool = _make_runner(
        first_reply=_first_llm_reply(tool_calls=tool_calls),
        final_reply=_final_llm_reply("Modelo não encontrado."),
        tool_result=tool_result,
    )

    state = _run(
        runner.run(
            "hermes3:3b",
            "Detalhes do modelo gpt-9000:xl: parâmetros e quantização.",
        )
    )

    assert state.used_tools == ["get_ollama_model_details"]
    assert state.tool_results == [tool_result]
    # Renderer was skipped because the result contained "error".
    assert state.response == "Modelo não encontrado."
    chat_messages.assert_awaited_once()
    call_kwargs = chat_messages.call_args.kwargs
    assert call_kwargs.get("format") is None


# --- Structured final synthesis: no renderer for this tool combo, but schema present ---
# We simulate this by using get_ollama_model_details (which has a renderer),
# then bypassing render_answer via a monkeypatched module-level function.


def test_run_final_synthesis_parses_structured_output_when_no_renderer(monkeypatch):
    from app.agent import runner as runner_module

    tool_calls = [
        {
            "function": {
                "name": "get_ollama_model_details",
                "arguments": {"model": "hermes3:3b"},
            }
        }
    ]
    tool_result = {
        "model": "hermes3:3b",
        "family": "llama",
        "parameter_size": "3B",
        "quantization_level": "Q4_0",
        "format": "gguf",
    }

    # Force the "no renderer" branch by making render_answer return None.
    monkeypatch.setattr(runner_module, "render_answer", lambda names, structured: None)

    # The LLM's final response is a JSON string matching the schema.
    final_json = '{"model":"hermes3:3b","family":"llama","parameter_size":"3B","quantization_level":"Q4_0","format":"gguf"}'

    runner, _, chat_messages, _ = _make_runner(
        first_reply=_first_llm_reply(tool_calls=tool_calls),
        final_reply=_final_llm_reply(final_json),
        tool_result=tool_result,
    )

    state = _run(
        runner.run(
            "hermes3:3b",
            "Detalhes do modelo hermes3:3b: parâmetros e quantização.",
        )
    )

    assert state.used_tools == ["get_ollama_model_details"]
    # With render_answer stubbed to None, response falls back to the LLM's raw content.
    assert state.response == final_json
    # And the final call was made WITH a format schema (tool had no error).
    call_kwargs = chat_messages.call_args.kwargs
    assert call_kwargs.get("format") is not None
