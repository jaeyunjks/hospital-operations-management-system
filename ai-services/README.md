# AI Services

Central AI capabilities shared across the five feature microservices.

| Directory            | Purpose                                   | Release   |
|----------------------|-------------------------------------------|-----------|
| `ai-mode/`           | Shared AI-Mode (Ollama-backed inference)  | Release 0 |
| `agentic-loop/`      | Shared Plan → Act → Observe → Adapt CLI   | Release 0 |
| `mcp-server/`        | Model Context Protocol server             | Release 1 |
| `rag-server/`        | Retrieval-Augmented Generation server     | Release 1 |
| `multi-agent-server/`| Multi-Agent System                        | Release 2 |

Approved open-source LLMs: Llama, Qwen and/or DeepSeek, run via Ollama.
The Release 0 agentic loop is intentionally single-model and validation-only.
Release 1 and Release 2 services remain placeholders.
