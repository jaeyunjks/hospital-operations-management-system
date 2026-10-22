"""Offline tests for azure_deploy.py: no Azure account, Docker or network needed.

``run`` is replaced by a recorder, so these check the exact Azure CLI
commands the script would send, which is what breaks a real deployment.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "azure_deploy.py"
spec = importlib.util.spec_from_file_location("azure_deploy", SCRIPT)
deploy = importlib.util.module_from_spec(spec)
sys.modules["azure_deploy"] = deploy
spec.loader.exec_module(deploy)

ROOT = deploy.REPO_ROOT


class FakeAzure:
    """Answers the few queries the script makes and records every command."""

    def __init__(self, existing=()):
        self.calls = []
        self.existing = set(existing)

    def __call__(self, args, *, capture=True, check=True, secret=False):
        cmd = [a for a in args[1:] if a != "--only-show-errors"]
        self.calls.append(cmd)
        joined = " ".join(cmd)
        if cmd[:2] == ["account", "show"]:
            return "12345678-aaaa-bbbb" if "--query" in cmd else json.dumps(
                {"name": "Azure subscription 1", "id": "12345678-aaaa-bbbb", "user": {"name": "me"}})
        if cmd[:2] == ["containerapp", "show"]:
            return cmd[cmd.index("-n") + 1] if cmd[cmd.index("-n") + 1] in self.existing else ""
        if cmd[:2] == ["acr", "show"]:
            return "homsr2.azurecr.io"
        if cmd[:3] == ["acr", "credential", "show"]:
            return json.dumps({"username": "homsr2", "passwords": [{"value": "pw"}]})
        if cmd[:2] == ["containerapp", "list"]:
            return json.dumps([{"name": s["app"], "fqdn": f"{s['app']}.example.azurecontainerapps.io",
                                "external": s["ingress"] == "external"} for s in deploy.SERVICES])
        if "env show" in joined:
            return ""
        return ""

    def find(self, *prefix):
        return [c for c in self.calls if c[:len(prefix)] == list(prefix)]


@pytest.fixture()
def fake(monkeypatch):
    azure = FakeAzure()
    monkeypatch.setattr(deploy, "run", azure)
    monkeypatch.setattr(deploy, "_tool", lambda name: name)
    return azure


def test_catalogue_points_at_real_dockerfiles_and_contexts():
    for service in deploy.SERVICES:
        assert (ROOT / service["dockerfile"]).is_file(), service["dockerfile"]
        assert (ROOT / service["context"]).is_dir(), service["context"]
    names = [s["app"] for s in deploy.SERVICES]
    assert len(names) == len(set(names)) == 16
    assert all(len(n) <= 32 and n == n.lower() for n in names)


def test_every_reference_targets_a_deployed_app():
    names = {s["app"] for s in deploy.SERVICES}
    for service in deploy.SERVICES:
        for value in service["env"].values():
            for ref in __import__("re").findall(r"\{([a-z0-9-]+)\}", value):
                assert ref in names, f"{service['app']} refers to unknown app {ref}"


def test_ai_services_are_off_in_every_backend():
    for service in deploy.SERVICES:
        env = service["env"]
        for key, value in env.items():
            if key.endswith("_ENABLED") and key != "AUTH_ENABLED":
                assert value == "false", f"{service['app']} {key}"
            if "OLLAMA" in key and key.endswith("URL"):
                assert value == deploy.AI_OFF
        assert "host.docker.internal" not in json.dumps(env)


def test_databases_are_internal_and_deployed_first():
    order = [s["role"] for s in deploy.select(None)]
    assert order == sorted(order, key=deploy.ROLE_ORDER.get)
    assert all(s["ingress"] == "internal" for s in deploy.SERVICES if s["role"] == "database")


def test_select_one_student_and_reject_unknown():
    assert [s["app"] for s in deploy.select(["student-4"])] == ["s4-db", "s4-api", "s4-web"]
    with pytest.raises(deploy.DeployError):
        deploy.select(["student-9"])


def test_placeholders_become_internal_http_urls():
    env = deploy.resolve_env({"A": "{s4-db}", "B": "{s1-api}/api", "C": "plain"})
    assert env == {"A": "http://s4-db", "B": "http://s1-api/api", "C": "plain"}


def test_deploy_creates_new_apps_with_registry_and_secrets(fake):
    args = deploy.parse_args(["deploy", "--only", "student-3", "--build", "skip", "--tag", "abc123"])
    deploy.cmd_deploy(args)
    creates = fake.find("containerapp", "create")
    assert [c[c.index("-n") + 1] for c in creates] == ["s3-db", "s3-api", "s3-web"]
    web = creates[-1]
    assert web[web.index("--image") + 1] == "homsr2.azurecr.io/homs-s3-web:abc123"
    assert web[web.index("--ingress") + 1] == "external"
    assert "--secrets" in web and any(a.startswith("flask-secret=") for a in web)
    assert "FLASK_SECRET_KEY=secretref:flask-secret" in web
    assert "BACKEND_API_URL=http://s3-api" in web
    db = creates[0]
    assert db[db.index("--ingress") + 1] == "internal"
    assert db[db.index("--min-replicas") + 1] == "1" and db[db.index("--max-replicas") + 1] == "1"
    insecure = [c[c.index("-n") + 1] for c in fake.find("containerapp", "ingress", "update")]
    assert insecure == ["s3-db", "s3-api"]


def test_redeploy_updates_without_new_secrets(fake):
    fake.existing.update({"s4-db", "s4-api", "s4-web"})
    deploy.cmd_deploy(deploy.parse_args(["deploy", "--only", "student-4", "--build", "skip", "--tag", "t2"]))
    assert fake.find("containerapp", "create") == []
    updates = fake.find("containerapp", "update")
    assert len(updates) == 3 and all("--secrets" not in u for u in updates)
    api = updates[1]
    assert "MULTI_AGENT_ENABLED=false" in api and "DATABASE_URL=http://s4-db" in api


def test_always_on_keeps_one_replica(fake):
    deploy.cmd_deploy(deploy.parse_args(["deploy", "--only", "s4-web", "--build", "skip", "--always-on"]))
    web = fake.find("containerapp", "create")[0]
    assert web[web.index("--min-replicas") + 1] == "1"


def test_docker_build_pushes_to_registry(fake):
    deploy.cmd_deploy(deploy.parse_args(["deploy", "--only", "s4-api", "--tag", "t3"]))
    calls = fake.calls
    assert ["acr", "login", "-n", deploy.acr_name()] in calls
    build = next(c for c in calls if c[:1] == ["build"])
    assert build[build.index("-t") + 1] == "homsr2.azurecr.io/homs-s4-api:t3"
    assert ["push", "homsr2.azurecr.io/homs-s4-api:t3"] in calls


def test_probe_file_matches_the_cloud_review_mode(fake, tmp_path):
    out = tmp_path / "probes.json"
    deploy.cmd_probes(deploy.parse_args(["probes", "--out", str(out)]))
    spec = json.loads(out.read_text(encoding="utf-8"))
    kinds = {p["kind"] for p in spec["probes"]}
    assert kinds == {"core", "crud", "ai_disabled"}
    services = {p["service"] for p in spec["probes"] if p["kind"] == "ai_disabled"}
    assert services == {"ai-mode", "mcp", "rag", "multi-agent"}
    assert all(p["url"].startswith("https://") for p in spec["probes"])

    sys.path.insert(0, str(ROOT / "ai-services" / "agentic-loop"))
    import cloud_review
    loaded = cloud_review.load_probes(out)
    assert len(loaded["probes"]) == len(spec["probes"])


def test_teardown_needs_confirmation(fake):
    with pytest.raises(deploy.DeployError):
        deploy.cmd_teardown(deploy.parse_args(["teardown"]))
    deploy.cmd_teardown(deploy.parse_args(["teardown", "--yes"]))
    assert fake.find("group", "delete")
