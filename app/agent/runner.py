import json
from typing import Any, Awaitable, Callable

from app.agent.prompts import (
    SYSTEM_PROMPT_NO_TOOLS_SIMPLE,
    SYSTEM_PROMPT_NO_TOOLS_STRICT,
    SYSTEM_PROMPT_WITH_TOOLS,
)
from app.agent.state import AgentState
from app.services.ollama import chat_messages as default_chat_messages
from app.services.ollama import chat_with_tools as default_chat_with_tools
from app.tools.registry import (
    TOOL_REGISTRY,
    TOOLS,
    answer_schema_for,
    deterministic_route,
    execute_tool as default_execute_tool,
    is_tool_allowed,
    render_answer,
)


ChatWithToolsFn = Callable[..., Awaitable[dict]]
ChatMessagesFn = Callable[..., Awaitable[dict]]
ExecuteToolFn = Callable[[str, Any], Awaitable[dict]]


class AgentRunner:
    """Encapsulates one /agent turn.

    Behavior is intentionally identical to the previous inline implementation
    in routes.py. Dependencies are injectable so the runner can be unit tested
    without HTTP or a live LLM.
    """

    def __init__(
        self,
        chat_with_tools_fn: ChatWithToolsFn = default_chat_with_tools,
        chat_messages_fn: ChatMessagesFn = default_chat_messages,
        execute_tool_fn: ExecuteToolFn = default_execute_tool,
    ) -> None:
        self._chat_with_tools = chat_with_tools_fn
        self._chat_messages = chat_messages_fn
        self._execute_tool = execute_tool_fn

    async def run(self, model: str, user_message: str) -> AgentState:
        state = AgentState(
            model=model,
            user_message=user_message,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT_WITH_TOOLS},
                {"role": "user", "content": user_message},
            ],
        )

        first_response = await self._chat_with_tools(
            model=model, messages=state.messages, tools=TOOLS
        )
        assistant_message = first_response.get("message", {})
        tool_calls = assistant_message.get("tool_calls", [])

        if not tool_calls:
            tool_calls = self._deterministic_route_to_tool_calls(user_message)

        if not tool_calls:
            tool_calls = self._textual_fallback_to_tool_calls(
                user_message, assistant_message.get("content", "")
            )

        if not tool_calls:
            state.response = await self._direct_answer(
                model, user_message, SYSTEM_PROMPT_NO_TOOLS_STRICT
            )
            return state

        state.messages.append(assistant_message)
        await self._execute_allowed_tool_calls(state, tool_calls, user_message)

        if not state.used_tools:
            state.response = await self._direct_answer(
                model, user_message, SYSTEM_PROMPT_NO_TOOLS_SIMPLE
            )
            return state

        rendered_direct = self._maybe_render_from_tool_result(state)
        if rendered_direct is not None:
            state.response = rendered_direct
            return state

        state.response = await self._llm_final_synthesis(state)
        return state

    def _deterministic_route_to_tool_calls(
        self, user_message: str
    ) -> list[dict]:
        route = deterministic_route(user_message)
        if route is None:
            return []
        name, args = route
        return [{"function": {"name": name, "arguments": args}}]

    def _textual_fallback_to_tool_calls(
        self, user_message: str, content: str
    ) -> list[dict]:
        # Textual fallback: only for zero-argument tools whose name the model
        # leaked into free-form content AND whose policy gate allows this
        # message. Anything else is treated as untrusted text.
        for tool_name, tool in TOOL_REGISTRY.items():
            required = tool.input_model.model_json_schema().get("required", [])
            if required:
                continue
            if tool_name in content and is_tool_allowed(tool_name, user_message):
                return [{"function": {"name": tool_name, "arguments": {}}}]
        return []

    async def _direct_answer(
        self, model: str, user_message: str, system_prompt: str
    ) -> str:
        response = await self._chat_messages(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        return response.get("message", {}).get("content", "")

    async def _execute_allowed_tool_calls(
        self,
        state: AgentState,
        tool_calls: list[dict],
        user_message: str,
    ) -> None:
        for tool_call in tool_calls:
            function = tool_call.get("function", {})
            tool_name = function.get("name")
            arguments = function.get("arguments", {})

            # Policy gate is per-tool and lives in the registry.
            # The LLM may propose a tool; the app decides whether to authorize it.
            if not is_tool_allowed(tool_name, user_message):
                continue

            result = await self._execute_tool(tool_name, arguments)

            state.used_tools.append(tool_name)
            state.tool_results.append(result)
            state.messages.append(
                {
                    "role": "tool",
                    "tool_name": tool_name,
                    "content": json.dumps(result),
                }
            )

    def _maybe_render_from_tool_result(self, state: AgentState) -> str | None:
        # Prefer deterministic rendering from the validated tool result:
        # the app is authoritative over data, the LLM is authoritative over language.
        # Skip the final LLM call entirely when a renderer exists — no room for hallucination.
        first_result = state.tool_results[0] if state.tool_results else {}
        if "error" in first_result:
            return None
        return render_answer(state.used_tools, first_result)

    async def _llm_final_synthesis(self, state: AgentState) -> str:
        # No renderer available → ask the LLM to synthesize,
        # constrained by the answer schema when the tool declares one.
        # If any tool returned an error, skip the schema so the LLM can
        # respond in free-form (e.g. "model not found").
        any_error = any("error" in r for r in state.tool_results)
        fmt = None if any_error else answer_schema_for(state.used_tools)

        final_response = await self._chat_messages(
            model=state.model, messages=state.messages, format=fmt
        )
        final_content = final_response.get("message", {}).get("content", "")

        if fmt is not None and final_content:
            try:
                structured = json.loads(final_content)
                rendered = render_answer(state.used_tools, structured)
                if rendered is not None:
                    return rendered
            except json.JSONDecodeError:
                pass

        return final_content
