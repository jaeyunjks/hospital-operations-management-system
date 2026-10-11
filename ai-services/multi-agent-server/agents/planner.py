from agents.base import StructuredAgent


class Planner(StructuredAgent):
    role = "planner"
    prompt_file = "planner.txt"
    required_fields = ("goal", "required_evidence", "planned_steps", "expected_result")
