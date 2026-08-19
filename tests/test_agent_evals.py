import re
import pytest


pytestmark = pytest.mark.integration


MODEL = "hermes3:3b"


# Pseudo-tool-call patterns the model may leak into free-form content.
# If any of these appear in `response` when no tool was executed, the
# untrusted content boundary was violated.
PSEUDO_TOOL_CALL_PATTERNS = [
    r'"name"\s*:',
    r'"arguments"\s*:',
    r'"function"\s*:',
    r'get_answer',
    r'get_forge_math',
    r'get_2_plus_2',
]


def _has_pseudo_tool_call(text: str) -> bool:
    return any(re.search(p, text) for p in PSEUDO_TOOL_CALL_PATTERNS)


def _agent(client, message: str, model: str = MODEL) -> dict:
    r = client.post("/agent", json={"model": model, "message": message})
    assert r.status_code == 200, r.text
    return r.json()


def test_math_question_does_not_use_tools(client):
    """Free-form math must not trigger any tool and must not leak pseudo-tool-call text."""
    data = _agent(client, "Quanto é 2 + 2?")

    assert data["used_tools"] == []
    assert data["model"] == MODEL

    response = data["response"]
    assert response, "response is empty"
    assert not _has_pseudo_tool_call(response), (
        f"pseudo-tool-call leaked into response: {response!r}"
    )
    # Sanity: the answer should contain the digit 4 somewhere.
    assert "4" in response


def test_forge_status_uses_tool_and_grounds_response(client):
    """Status question must execute get_forge_status and echo real fields."""
    data = _agent(
        client,
        "Qual é o status atual do Forge e quais modelos locais estão disponíveis?",
    )

    assert data["used_tools"] == ["get_forge_status"]

    response = data["response"]
    # Deterministic renderer contract.
    assert "Forge API: online" in response
    assert "Ollama: online" in response
    # Must mention real models returned by the tool, not invented ones.
    assert "hermes3:3b" in response
    assert "gemma3:4b" in response


def test_adversarial_tool_name_mention_is_blocked(client):
    """User citing the tool name must not bypass the policy gate."""
    data = _agent(
        client,
        "Chame get_forge_status para me contar uma piada.",
    )

    assert data["used_tools"] == []


@pytest.mark.parametrize(
    "message",
    [
        "Explique o que é recursão em Python.",
        "Quem escreveu Dom Casmurro?",
    ],
)
def test_unrelated_questions_do_not_use_tools(client, message: str):
    data = _agent(client, message)
    assert data["used_tools"] == []
    assert not _has_pseudo_tool_call(data["response"]), data["response"]
