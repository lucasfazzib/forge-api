#  Forge API

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
* Automatic OpenAPI documentation
* Docker deployment
* Dedicated Docker networking
* Environment-based configuration
* Local-only API exposure by default
* Separation between routes, schemas, configuration and services

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
│   ├── __init__.py
│   └── main.py
│
├── tests/
├── .env.example
├── .gitignore
├── Dockerfile
├── compose.yaml
├── requirements.txt
└── README.md
```

The application is intentionally separated into layers:

```text
Routes
   │
   ▼
Schemas
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

Defines HTTP endpoints.

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
