# Student 2 knowledge — Clinical Staff Management

Markdown files in this folder (except this README) are indexed by the shared
RAG server and searched when a request uses `"feature": "student-2"`.
Documents in `knowledge/shared/` are always searched as well.

## Writing a document

- One topic per file, named in kebab case, for example `admission-workflow.md`.
- Start with one `# Title` line, then use `##` sections. Each section becomes a
  retrievable chunk, and its heading appears in citations, so make headings
  specific (`## Discharge approval`, not `## Notes`).
- Keep sections to a few short paragraphs about one rule or process.
- Describe how **this feature actually behaves**: statuses, roles, limits,
  calculations and validation rules taken from the code. Do not add generic
  medical or clinical advice.
- Never include real patient data, credentials or secrets.

After adding or editing documents, rebuild the index from the repository root:

```bash
python3 ai-services/rag-server/ingest.py
```
