#  Forge API

[![CI](https://github.com/lucasfazzib/forge-api/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/lucasfazzib/forge-api/actions/workflows/ci.yml)

A lightweight, containerized API for interacting with **local Large Language Models through Ollama**.

Forge API is part of the **Forge Local AI Lab** and provides a clean HTTP abstraction between applications and locally hosted LLMs.

The project is designed around a simple principle:

> Applications should communicate with a stable API — not directly with the model runtime.

---

## Overview

Forge API currently provides:

* FastAPI-based HTTP API
* Local LLM inference through Ollama
* Model discovery
* Chat/generation endpoint
* Agent loop with typed tool registry, policy gate and structured outputs
* Automatic OpenAPI documentation
* Docker deployment
* Dedicated Docker networking
* Environment-based configuration
* Local-only API exposure by default
* Separation between routes, schemas, configuration and services
* Unit and integration test suites, wired into GitHub Actions CI

Current version:

```text
0.1.0
```

---

## Architecture

```text
                    Client
                      │
                      │ HTTP
                      ▼
              ┌────────────────┐
              │   Forge API    │
              │    FastAPI     │
              │    Docker      │
              └───────┬────────┘
                      │
                      │ HTTP
                      ▼
              ┌────────────────┐
              │     Ollama     │
              │   Linux Host   │
              └───────┬────────┘
                      │
                      ▼
                Local Models
                      │
              ┌───────┴───────┐
              │               │
         Hermes 3 3B      Gemma 3 4B
```

In the current Forge environment:

```text
Client
   │
   ▼
127.0.0.1:8000
   │
   ▼
Forge API
[Docker Container]
   │
   ▼
forge-net
172.30.0.0/24
   │
   ▼
host.docker.internal:11434
   │
   ▼
Ollama
   │
   ▼
Local LLM
```

---

## Project Structure

```text
forge-api/
├── app/
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── prompts.py
│   │   ├── runner.py
│   │   └── state.py
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   └── routes.py
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   └── config.py
│   │
│   ├── schemas/
│   │   ├── __init__.py
│   │   └── chat.py
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   └── ollama.py
│   │
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── registry.py
│   │   └── system.py
│   │
│   ├── __init__.py
│   └── main.py
│
├── tests/
│   ├── conftest.py
│   ├── test_agent_evals.py
│   ├── test_agent_runner.py
│   └── test_registry.py
│
├── .github/
│   └── workflows/
│       └── ci.yml
│
├── .env.example
├── .gitignore
├── Dockerfile
├── compose.yaml
├── pytest.ini
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

The application is intentionally separated into layers:

```text
Routes
   │
   ▼
AgentRunner (app/agent/)
   │
   ▼
Tool Registry (app/tools/)
   │
   ▼
Services
   │
   ▼
Ollama
```

### `app/main.py`

Creates the FastAPI application and registers the API router.

### `app/api/`

Defines HTTP endpoints. The `/agent` route is intentionally thin: it delegates the full agent loop to `AgentRunner` and only translates the result into an HTTP response.

### `app/agent/`

The agent runtime. Contains the manual agent loop extracted from the HTTP layer so it can be unit tested without a live LLM.

* `state.py` — `AgentState`, the mutable state carried through one `/agent` turn.
* `prompts.py` — system prompts used by the runner (with tools, no tools strict, no tools simple).
* `runner.py` — `AgentRunner`, encapsulates the loop: first LLM call with tools → deterministic router → textual fallback → policy gate → tool execution → deterministic rendering or LLM final synthesis. Dependencies (`chat_with_tools`, `chat_messages`, `execute_tool`) are constructor-injected so tests can replace them with `AsyncMock`.

### `app/schemas/`

Contains Pydantic request and response schemas.

### `app/services/`

Contains integration logic with external services such as Ollama.

### `app/core/`

Contains application configuration.

---

## API Endpoints

### Health

```http
GET /health
```

Example:

```bash
curl http://127.0.0.1:8000/health
```

Response:

```json
{
  "status": "ok",
  "service": "forge-api",
  "version": "0.1.0"
}
```

---

### Models

```http
GET /models
```

Returns the models currently available through Ollama.

Example:

```bash
curl http://127.0.0.1:8000/models
```

Example models in the current Forge environment:

```text
hermes3:3b
gemma3:4b
```

---

### Chat

```http
POST /chat
```

Example:

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "model": "hermes3:3b",
    "message": "Explain what a vector database is."
  }'
```

Response:

```json
{
  "model": "hermes3:3b",
  "response": "..."
}
```

---

### Agent

```http
POST /agent
```

Runs the message through the agent loop: the model may propose tool calls,
the application decides whether to allow them, executes them explicitly, and
either renders a deterministic answer from the tool result or asks the model
for a plain answer without tools.

Example — free-form question, no tool executed:

```bash
curl -X POST http://127.0.0.1:8000/agent \
  -H "Content-Type: application/json" \
  -d '{"model": "hermes3:3b", "message": "Quanto é 2 + 2?"}'
```

```json
{"model": "hermes3:3b", "used_tools": [], "response": "2 plus 2 é 4."}
```

Example — status question, tool executed, response rendered from validated data:

```bash
curl -X POST http://127.0.0.1:8000/agent \
  -H "Content-Type: application/json" \
  -d '{"model": "hermes3:3b", "message": "Qual é o status atual do Forge?"}'
```

```json
{
  "model": "hermes3:3b",
  "used_tools": ["get_forge_status"],
  "response": "Forge API: online. Ollama: online. Modelos locais instalados: hermes3:3b, gemma3:4b."
}
```

Example — model details question, tool executed, arguments extracted from the message:

```bash
curl -X POST http://127.0.0.1:8000/agent \
  -H "Content-Type: application/json" \
  -d '{"model": "hermes3:3b", "message": "Detalhes do modelo hermes3:3b: parâmetros e quantização."}'
```

```json
{
  "model": "hermes3:3b",
  "used_tools": ["get_ollama_model_details"],
  "response": "Modelo: hermes3:3b. Família: llama. Parâmetros: 3.2B. Quantização: Q4_K_M. Formato: gguf."
}
```

---

## Agent Architecture

The `/agent` endpoint implements a manual agent loop. No agent framework is used.

```text
User message
     │
     ▼
LLM call #1 (with tools spec)
     │
     ├── structured tool_calls? ── no ── deterministic router matches? ── no ── allowlisted textual fallback? ── no ──▶ LLM call #2 (without tools) ──▶ direct answer
     │                                          │                                             │
     │                                          yes                                           yes (zero-arg tool + gate)
     │                                          │                                             │
     └── yes ◀──────────────────────────────────┴─────────────────────────────────────────────┘
              │
              ▼
        Policy gate on user intent
              │
              ├── denied ──▶ used_tools=[] ──▶ LLM call #2 (without tools)
              │
              allowed
              │
              ▼
        execute_tool()
          - reject unknown tools (registry authoritative)
          - validate arguments (Pydantic input_model)
          - call handler (HTTP errors become structured tool errors)
          - validate result (Pydantic output_model)
              │
              ▼
        Deterministic renderer available for this tool?
              │
              ├── yes ──▶ render text from validated tool result ──▶ response
              │
              no
              │
              ▼
        LLM call #3 with format=answer_schema (Ollama structured output)
              │
              ▼
        parse & (optionally) render ──▶ response
```

### Trust boundaries

The LLM output is treated as untrusted input at every step:

1. **Registry is authoritative.** `execute_tool` only runs functions in `TOOL_REGISTRY`. A function name proposed by the model is never resolved dynamically.
2. **Policy gate on user intent, not on model output.** Each tool declares its own `allow(message)` predicate. The gate reads the original user message. Literal mentions of any registered tool name are stripped from the message before matching keywords, so an adversarial or accidental mention of a tool name cannot by itself satisfy the gate.
3. **Typed contracts on both sides.** `arguments` (LLM → app) are validated against the tool's `input_model` before execution; the handler's return value is validated against the `output_model` before being sent back to the LLM. HTTP errors from tool handlers are captured as structured error results, not propagated to the client.
4. **Deterministic rendering over LLM synthesis.** When a validated tool result is available and the tool declares an `answer_renderer`, the final LLM call is skipped and the response is rendered from the tool result. Zero room for hallucination.
5. **App-side deterministic router as a fallback.** Small models like `hermes3:3b` have unreliable tool selection when more than one tool is offered. When the LLM fails to emit a valid `tool_call`, the app checks whether exactly one tool's policy gate matches the user message and whether the tool's `arg_extractor` can pull required arguments from the message. If so, the app invokes the tool explicitly. This is the trust boundary that says: the app owns routing when the model is unreliable.

### Tool registry

Tools are declared in `app/tools/registry.py` as `RegisteredTool` entries:

```python
RegisteredTool(
    name="get_ollama_model_details",
    description="...",
    input_model=ModelDetailsInput,       # extra='forbid', requires `model`
    output_model=ModelDetailsOutput,     # extra='forbid'
    handler=get_ollama_model_details,
    answer_model=ModelDetailsOutput,
    answer_renderer=_render_model_details,
    allow=_model_details_allow,          # policy gate for this tool
    arg_extractor=_extract_model_details_args,  # deterministic router support
)
```

The tool spec exposed to the LLM (`TOOLS`) is generated from `input_model.model_json_schema()` — a single source of truth. Adding a new tool means adding one `RegisteredTool` entry and nothing else in the routing layer.

Currently registered tools:

* `get_forge_status` — zero-argument tool that reports Forge API, Ollama and installed local model tags.
* `get_ollama_model_details` — takes `model: str`, returns family, parameter size, quantization level, format.

---

## Testing

Two suites, separated by intent.

**Unit tests** — `tests/test_registry.py` and `tests/test_agent_runner.py`. No Ollama, no HTTP. Cover the registry contracts (unknown tool rejection, argument validation, handler output validation, JSON Schema generation, deterministic renderer, per-tool policy gate, deterministic router) and the agent runner branches (no tool call → direct answer, structured tool call → deterministic rendering, denied tool call → direct answer, deterministic router injection, textual fallback, tool error result → LLM final without schema, structured final synthesis). Run in CI.

**Integration tests / evals** — `tests/test_agent_evals.py`. Hit the running `/agent` endpoint against a real Ollama backend. Cover: free-form math (no tool, no pseudo-tool-call leakage), status question (tool executed, response grounded in tool result), model details question (tool executed via deterministic router, argument extracted from the message), adversarial tool name mentions (blocked), unrelated knowledge questions (no tool). Marked with `@pytest.mark.integration`. Not run in CI (yet).

Install dev dependencies:

```bash
pip install -r requirements-dev.txt
```

Run unit tests only:

```bash
pytest -m "not integration"
```

Run integration tests (requires `docker compose up -d --build forge-api` and Ollama):

```bash
pytest -m integration
```

Point tests at a different host:

```bash
FORGE_API_URL=http://127.0.0.1:8000 pytest -m integration
```

---

## Continuous Integration

GitHub Actions workflow at `.github/workflows/ci.yml` runs on push and pull request to `main` and `dev`:

* **test** — installs dependencies, prints collected unit tests, then runs `pytest -m "not integration"`. Configured with `--strict-markers` and `--strict-config` to fail loudly on misconfiguration (defense against false-green).
* **docker-build** — builds the Docker image with Buildx to guarantee the Dockerfile stays valid. Does not push.

Integration tests are not part of CI because the runner has no Ollama backend. They are run locally against the containerized Forge API.

---

## Development Workflow

Three long-lived branches:

```text
feature branches  ─▶  dev  ─▶  main
     (work)         (integration)   (release)
```

* `main` — protected. Only receives merges from `dev` (release) or focused hotfix branches. This is what production/self-hosted deployments track.
* `dev` — integration branch. Receives feature and fix branches. CI must be green before merging into `main`.
* `feat/*`, `fix/*`, `chore/*` — short-lived working branches, cut from `dev`.

Typical flow:

```bash
git checkout dev
git pull --ff-only
git checkout -b feat/short-descriptive-name

# work, commit, push
git push -u origin feat/short-descriptive-name

# open a PR: feat/... -> dev
# CI runs; after review + green CI, merge into dev.

# when a set of changes on dev is ready to promote:
# open a PR: dev -> main
```

Commit messages follow Conventional Commits (`feat:`, `fix:`, `chore:`, `test:`, `docs:`).

---

## Interactive API Documentation

FastAPI automatically exposes OpenAPI documentation.

With Forge API running, open:

```text
http://127.0.0.1:8000/docs
```

This provides an interactive Swagger UI where endpoints can be inspected and tested.

The OpenAPI specification is available at:

```text
http://127.0.0.1:8000/openapi.json
```

---

## Running Locally

### Requirements

* Python 3
* Ollama
* At least one Ollama model

Create a virtual environment:

```bash
python3 -m venv .venv
```

Activate it:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the API:

```bash
uvicorn app.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  --reload
```

Test:

```bash
curl http://127.0.0.1:8000/health
```

---

## Docker

Forge API is designed to run as a container.

Build and start:

```bash
sudo docker compose up -d --build
```

Check status:

```bash
sudo docker compose ps
```

View logs:

```bash
sudo docker compose logs -f forge-api
```

Stop:

```bash
sudo docker compose down
```

Rebuild after application changes:

```bash
sudo docker compose up -d --build
```

---

## Docker Networking

Forge API uses the existing Forge infrastructure network:

```text
forge-infra_forge-net
```

The network currently uses:

```text
172.30.0.0/24
```

The application reaches Ollama on the Linux host using:

```text
host.docker.internal:11434
```

The mapping is provided through Docker's host gateway functionality.

Conceptually:

```text
Forge API container
        │
        ▼
Docker bridge
        │
        ▼
host.docker.internal
        │
        ▼
Ollama :11434
```

---

## Configuration

Application configuration is environment-driven.

Current variable:

```text
OLLAMA_URL
```

Local development default:

```text
http://127.0.0.1:11434
```

Docker:

```text
http://host.docker.internal:11434
```

Secrets should be supplied through environment variables or an ignored `.env` file when required in the future.

Never commit real credentials.

---

## Security

Forge API follows a **local-first and least-exposure** approach.

The Docker port is intentionally published as:

```text
127.0.0.1:8000:8000
```

instead of:

```text
0.0.0.0:8000:8000
```

This means the API is intended to be accessible only from the Forge host unless explicitly configured otherwise.

Current security principles include:

* Local-only API exposure
* Dedicated Docker network
* Firewall-controlled access to Ollama
* No credentials committed to Git
* Environment-based configuration
* No unrestricted host access from the application
* No Docker socket mounted into the API
* No privileged container mode
* Human approval intended for future critical agent actions

Before committing changes:

```bash
git status
git diff --cached
```

Never commit:

```text
.env
private keys
passwords
API keys
access tokens
credentials
database dumps
private datasets
private documents
```

---

## Current Stack

| Component         | Purpose                 |
| ----------------- | ----------------------- |
| Python            | Application runtime     |
| FastAPI           | HTTP API                |
| Pydantic          | Request validation      |
| HTTPX             | Async HTTP client       |
| Uvicorn           | ASGI server             |
| Docker            | Application container   |
| Docker Compose    | Container orchestration |
| Ollama            | Local model runtime     |
| OpenAPI / Swagger | API documentation       |

---

## Current Models

The Forge host currently runs small quantized models suitable for local experimentation.

```text
Hermes 3 3B
Gemma 3 4B
```

Forge API itself is model-agnostic.

Any model exposed through the configured Ollama instance can be requested through the API.

---

## Development Flow

```text
Code
 │
 ▼
VS Code
 │
 ▼
Forge API
 │
 ▼
Docker
 │
 ▼
Ollama
 │
 ▼
Local Model
```

Projects are developed under:

```text
/data/projects/
```

while persistent infrastructure data remains outside the Git repositories.

---

## Roadmap

### v0.1 — Local LLM Gateway

* [x] FastAPI application
* [x] Health endpoint
* [x] Model discovery
* [x] Chat endpoint
* [x] Ollama integration
* [x] Async HTTP communication
* [x] Docker image
* [x] Docker Compose
* [x] Dedicated Forge network
* [x] Local-only API binding
* [x] Swagger documentation

### v0.2 — API Hardening

* [ ] Typed response schemas
* [ ] Structured error handling
* [ ] Request IDs
* [ ] Structured logging
* [ ] Latency metrics
* [ ] Model validation
* [ ] Automated tests
* [ ] Container health check
* [ ] Pinned dependency versions
* [ ] Pinned base image
* [ ] Configuration validation

### v0.3 — Streaming & Conversations

* [ ] Streaming responses
* [ ] Ollama chat API integration
* [ ] Conversation history
* [ ] System prompts
* [ ] Generation parameters
* [ ] Model configuration

### v0.4 — Knowledge / RAG

```text
Documents
    │
    ▼
Ingestion
    │
    ▼
Chunking
    │
    ▼
Embeddings
    │
    ▼
Vector Store
    │
    ▼
Retriever
    │
    ▼
Forge API
    │
    ▼
Local LLM
```

Planned features:

* [ ] Document ingestion
* [ ] Embeddings
* [ ] Vector search
* [ ] Retrieval-Augmented Generation
* [ ] Local knowledge bases
* [ ] Source attribution
* [ ] Evaluation

### v0.5 — Agents

Future architecture:

```text
Application
     │
     ▼
Forge API
     │
     ▼
Agent Layer
     │
 ┌───┼─────────────┐
 │   │             │
RAG Tools       Jobs
 │   │             │
 └───┼─────────────┘
     ▼
Local LLM
```

Agents will follow a restricted execution model.

The LLM will **not** receive unrestricted root or shell access to the Forge host.

Critical actions should require explicit human approval.

---

## Design Philosophy

Forge API is deliberately small.

The objective is not to create another large AI framework.

The objective is to provide a stable boundary between:

```text
Applications
     │
     ▼
Forge API
     │
     ▼
AI Infrastructure
```

This allows applications to evolve independently from the underlying model runtime.

Today the backend is Ollama.

Future implementations may include:

```text
Local Ollama models
Remote inference APIs
Specialized embedding models
RAG pipelines
Agents
Tool execution
Background workers
```

without requiring every application to integrate directly with each provider.

---

## Related Project

Forge API is part of the broader **Forge Local AI Lab**.

Infrastructure components such as Docker networking, Open WebUI, Ollama connectivity and host configuration are maintained separately in:

```text
forge-infra
```

---

## Status

```text
Forge API v0.1.0

HTTP API       ONLINE
Docker         ONLINE
Ollama         CONNECTED
Local Models   AVAILABLE
```

 **BUILD > INSTALL**
