# Student 3 backend prompts

Prompts are versioned as `<name>_v<number>.md`. Feature code must load a
checked-in prompt through `services.ai_client.run_prompt`, so every call records
the prompt name and version in its result and terminal log. Do not modify an
existing prompt version; add a new version when its wording or output contract
changes.

`smoke_v1.md` is an infrastructure-only connectivity check, not a pharmacy
feature prompt.

`expiry_advisory_v2.md` supersedes v1: it limits each reasoning field to one
short sentence (about 20 words) so the six-batch advisory completes promptly.
