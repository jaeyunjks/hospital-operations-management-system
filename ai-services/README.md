# AI Services

Central AI capabilities shared across the five feature microservices.
Release 1 implements the shared `mcp-server/` and `rag-server/`; both run locally,
outside Docker Compose.

| Directory            | Purpose                                   | Release   |
|----------------------|-------------------------------------------|-----------|
| `ai-mode/`           | Shared AI-Mode (Ollama-backed inference)  | Release 0 |
| `mcp-server/`        | Model Context Protocol server             | Release 1 |
| `rag-server/`        | Retrieval-Augmented Generation server     | Release 1 |
| `multi-agent-server/`| Multi-Agent System                        | Release 2 |

Approved open-source LLMs: Llama, Qwen and/or DeepSeek, run via Ollama.
Do not implement AI workflows until the relevant release is planned.
