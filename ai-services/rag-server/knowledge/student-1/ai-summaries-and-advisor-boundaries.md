# AI Summaries and Advisor Boundaries

## Patient administration summary

The patient profile summary is built from that patient's administrative-note text and admission dates, scheduled end dates, discharge dates, and statuses. When values are absent, the summary input uses labels such as `No notes recorded`, `No admissions recorded`, `date not set`, or `not recorded`. The profile refresh action reads a current snapshot and regenerates the summary.

## Summary generation and fallback

The backend summary prompt asks the local Ollama model for fewer than 100 words in clear language, using only supplied information and adding no advice, assumptions, or formatting. The model response is requested with temperature `0.2` and a configured timeout. If Ollama is unavailable, the deterministic fallback returns the first three sentences of normalized input, truncated to 500 characters. If the frontend cannot obtain a successful backend summary, it displays the original summary input instead.

## Applying a summary

The profile page displays the generated summary and offers an explicit Apply Summary action. Applying it submits the displayed text as a new patient administrative note. That note is included in the input when later summaries are generated.

## Decision-support boundary

This feature provides a summary of supplied patient-administration notes and admission metadata; it is not a clinical advisor and does not make admission or identity-review decisions. The separate RAG guidance endpoint accepts a bounded question and searches indexed workflow documents; it does not receive patient notes or profile data. RAG answers must not be used to determine patient identity or provide treatment advice.