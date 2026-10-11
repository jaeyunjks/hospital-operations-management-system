from agents.base import StructuredAgent


class Reviewer(StructuredAgent):
    role = "reviewer"
    prompt_file = "reviewer.txt"
    required_fields = (
        "review_status",
        "validated_findings",
        "risks",
        "unsupported_claims",
        "recommendation",
        "human_approval_required",
    )
