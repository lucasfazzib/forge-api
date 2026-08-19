from pydantic import BaseModel


class ChatRequest(BaseModel):
    model: str
    message: str

class AgentRequest(BaseModel):
    model: str = "hermes3:3b"
    message: str