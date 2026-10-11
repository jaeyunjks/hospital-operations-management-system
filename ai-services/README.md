# AI Services

Central AI capabilities shared across the five feature microservices.
Release 1 implements the shared `mcp-server/` and `rag-server/`; Release 2
implements role-specific, schema-validated local Ollama execution plus a shared
HTTP interface in the `multi-agent-server/` foundation. All run locally,
outside Docker Compose.

| Directory            | Purpose                                   | Release   |
|----------------------|-------------------------------------------|-----------|
| `ai-mode/`           | Shared AI-Mode (Ollama-backed inference)  | Release 0 |
| `mcp-server/`        | Model Context Protocol server             | Release 1 |
| `rag-server/`        | Retrieval-Augmented Generation server     | Release 1 |
| `multi-agent-server/`| Planner/Worker/Reviewer + human-review foundation | Release 2 |

Approved open-source LLMs: Llama, Qwen and/or DeepSeek, run via Ollama.
The Multi-Agent workflow uses `llama3.1:8b` for planning,
`llama3.2:3b` for work, and `deepseek-r1:8b` for independent review. It still
uses supplied evidence only. Its CLI and local HTTP API share the same
orchestrator and persistent Human Review lifecycle. MCP, RAG, feature API,
database and cloud integration remain later Release 2 work.
