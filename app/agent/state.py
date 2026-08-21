from pydantic import BaseModel, Field


class AgentState(BaseModel):
    """Mutable state carried through one /agent turn.

    Kept intentionally minimal now; will grow an `events` field in the
    observability pass and become the state graph node in the LangGraph pass.
    """

    model: str
    user_message: str
    messages: list[dict] = Field(default_factory=list)
    used_tools: list[str] = Field(default_factory=list)
    tool_results: list[dict] = Field(default_factory=list)
    response: str = ""
