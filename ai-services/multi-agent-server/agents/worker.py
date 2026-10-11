from agents.base import StructuredAgent


class Worker(StructuredAgent):
    role = "worker"
    prompt_file = "worker.txt"
    required_fields = ("status", "proposed_solution", "mutation_executed")
