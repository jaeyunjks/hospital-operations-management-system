#!/usr/bin/env python3
"""DEPRECATED legacy Azure deployment prototype.

This module is retained only so historical deployment evidence and its offline
tests remain reproducible. It is not used by the active GitHub workflow and is
not an approved Release 2 deployment entrypoint: it uses the ACR admin account,
does not mount Azure Files for every SQLite service, and implements a different
resource-naming contract.

Use the authoritative scripts instead:

    ./scripts/deploy/azure-foundation.sh
    ./scripts/deploy/github-oidc-setup.sh
    ./scripts/deploy/deploy-student.sh STUDENT
    ./scripts/deploy/deployment-testing.sh STUDENT

Historical prototype usage follows for reference only.

One shared Azure environment, and each student can deploy their own slice:

    python scripts/deploy/azure_deploy.py setup                 # once, by the subscription owner
    python scripts/deploy/azure_deploy.py deploy                # every service
    python scripts/deploy/azure_deploy.py deploy --only student-4
    python scripts/deploy/azure_deploy.py status                # URLs of every app
    python scripts/deploy/azure_deploy.py smoke                 # check the cloud app, save evidence
    python scripts/deploy/azure_deploy.py probes                # probe file for the Cloud Review mode
    python scripts/deploy/azure_deploy.py teardown --yes        # delete everything (stops all cost)

Target: Azure Container Apps (Consumption) + Azure Container Registry, one
resource group. Databases are internal only; backends and frontends are
public over HTTPS. AI-Mode, MCP, RAG and the Multi-Agent Server are disabled
by default in every service, which is the required Release 2 cloud baseline.

The script only needs Python 3 and the Azure CLI (``az``), so it runs the
same on Windows, macOS and Linux. It is no longer called by
``cloud-deployment.yml``.
Images are built with local Docker and pushed by default; ``--build acr``
builds in Azure instead, and ``--build skip`` reuses images already pushed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess  # noqa: S404 - runs the Azure CLI and Docker with fixed argument lists
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = REPO_ROOT / "docs" / "test-evidence" / "cloud"

# Shared environment names. Override with environment variables when needed.
RESOURCE_GROUP = os.environ.get("HOMS_AZURE_RESOURCE_GROUP", "homs-r2-rg")
LOCATION = os.environ.get("HOMS_AZURE_LOCATION", "australiaeast")
ENVIRONMENT = os.environ.get("HOMS_AZURE_ENVIRONMENT", "homs-r2-env")
ACR_NAME = os.environ.get("HOMS_AZURE_ACR")  # derived from the subscription when unset

# AI services are not hosted in the cloud baseline. Every AI client points at
# a closed local port, so a call fails immediately instead of hanging.
AI_OFF = "http://127.0.0.1:9"

# --------------------------------------------------------------------------- #
# Service catalogue: one entry per container app.
# "{s4-db}" style placeholders become http://<app name> (internal service
# discovery inside the Container Apps environment).
# --------------------------------------------------------------------------- #

SERVICES: List[Dict[str, Any]] = [
    # --- student-1: Patient & Admissions ---------------------------------
    {"app": "s1-db", "student": "student-1", "role": "database", "port": 6100, "ingress": "internal",
     "context": "student-1", "dockerfile": "student-1/database/Dockerfile",
     "env": {"HOST": "0.0.0.0", "PORT": "6100", "FLASK_DEBUG": "0"}},  # noqa: S104 - container bind
    {"app": "s1-api", "student": "student-1", "role": "backend", "port": 5100, "ingress": "external",
     "context": "student-1", "dockerfile": "student-1/backend/Dockerfile", "health": "/health",
     "env": {"HOST": "0.0.0.0", "PORT": "5100", "DATABASE_URL": "{s1-db}", "OLLAMA_URL": AI_OFF,  # noqa: S104
             "MCP_ENABLED": "false", "RAG_ENABLED": "false", "AUTH_ENABLED": "0", "FLASK_DEBUG": "0"}},
    {"app": "s1-web", "student": "student-1", "role": "frontend", "port": 3100, "ingress": "external",
     "context": "student-1", "dockerfile": "student-1/frontend/Dockerfile", "health": "/",
     "env": {"HOST": "0.0.0.0", "PORT": "3100", "BACKEND_URL": "{s1-api}", "FLASK_DEBUG": "0"}},  # noqa: S104

    # --- student-2: Clinical Staff ---------------------------------------
    {"app": "s2-db", "student": "student-2", "role": "database", "port": 6200, "ingress": "internal",
     "context": "student-2/database", "dockerfile": "student-2/database/Dockerfile",
     "env": {"PORT": "6200", "DATABASE_PATH": "/data/clinical_staff.db"}},
    {"app": "s2-api", "student": "student-2", "role": "backend", "port": 5200, "ingress": "external",
     "context": "student-2/backend", "dockerfile": "student-2/backend/Dockerfile", "health": "/health",
     "env": {"DATABASE_API_URL": "{s2-db}", "PATIENT_ADMISSION_API_URL": "{s1-api}/api",
             "STAFF_SHIFT_API_URL": "{s5-api}/api", "ROOM_BED_API_URL": "{s4-api}/api",
             "USE_EXTERNAL_STUBS": "false", "OLLAMA_BASE_URL": AI_OFF,
             "MCP_ENABLED": "false", "RAG_ENABLED": "false"}},
    {"app": "s2-web", "student": "student-2", "role": "frontend", "port": 3200, "ingress": "external",
     "context": ".", "dockerfile": "student-2/frontend/Dockerfile", "health": "/health",
     "env": {"BACKEND_API_URL": "{s2-api}"}},

    # --- student-3: Pharmacy & Medication Inventory -----------------------
    {"app": "s3-db", "student": "student-3", "role": "database", "port": 6300, "ingress": "internal",
     "context": ".", "dockerfile": "student-3/database/Dockerfile",
     "env": {"PORT": "6300", "STUDENT3_DATABASE_PATH": "/tmp/pharmacy.db"}},  # noqa: S108 - ephemeral demo DB
    {"app": "s3-api", "student": "student-3", "role": "backend", "port": 5300, "ingress": "external",
     "context": ".", "dockerfile": "student-3/backend/Dockerfile", "health": "/health",
     "env": {"PORT": "5300", "DATABASE_URL": "{s3-db}", "AGENT_ENABLED": "false", "AI_ENABLED": "false",
             "OLLAMA_URL": AI_OFF, "MCP_ENABLED": "false", "RAG_ENABLED": "false",
             "MULTI_AGENT_ENABLED": "false", "FLASK_DEBUG": "0"}},
    {"app": "s3-web", "student": "student-3", "role": "frontend", "port": 3300, "ingress": "external",
     "context": ".", "dockerfile": "student-3/frontend/Dockerfile", "health": "/",
     "secrets": {"flask-secret": "FLASK_SECRET_KEY"},  # pragma: allowlist secret
     "env": {"PORT": "3300", "BACKEND_API_URL": "{s3-api}", "BACKEND_API_TIMEOUT": "60", "FLASK_DEBUG": "0"}},

    # --- student-4: Room & Bed ---------------------------------------------
    {"app": "s4-db", "student": "student-4", "role": "database", "port": 6400, "ingress": "internal",
     "context": "student-4/database", "dockerfile": "student-4/database/Dockerfile",
     "env": {"PORT": "6400", "DB_PATH": "/app/rooms.db", "FLASK_DEBUG": "0"}},
    {"app": "s4-api", "student": "student-4", "role": "backend", "port": 5400, "ingress": "external",
     "context": "student-4/backend", "dockerfile": "student-4/backend/Dockerfile", "health": "/health",
     "env": {"PORT": "5400", "DATABASE_URL": "{s4-db}", "OLLAMA_URL": AI_OFF, "AUTH_ENABLED": "0",
             "MCP_ENABLED": "false", "RAG_ENABLED": "false", "MULTI_AGENT_ENABLED": "false",
             "FLASK_DEBUG": "0"}},
    {"app": "s4-web", "student": "student-4", "role": "frontend", "port": 3400, "ingress": "external",
     "context": ".", "dockerfile": "student-4/frontend/Dockerfile", "health": "/health",
     "env": {"PORT": "3400", "BACKEND_URL": "{s4-api}", "FLASK_DEBUG": "0"}},

    # --- student-5: Staff & Shift ------------------------------------------
    {"app": "s5-db", "student": "student-5", "role": "database", "port": 6500, "ingress": "internal",
     "context": ".", "dockerfile": "student-5/database/Dockerfile",
     "env": {"DATABASE_SERVICE_PORT": "6500", "STUDENT5_DATABASE_PATH": "/tmp/staff_shift.db"}},  # noqa: S108
    {"app": "s5-api", "student": "student-5", "role": "backend", "port": 5500, "ingress": "external",
     "context": ".", "dockerfile": "student-5/backend/Dockerfile", "health": "/health",
     "env": {"BACKEND_PORT": "5500", "DATABASE_SERVICE_URL": "{s5-db}", "AI_ENABLED": "false",
             "OLLAMA_URL": AI_OFF, "MCP_ENABLED": "false", "RAG_ENABLED": "false",
             "MULTI_AGENT_BASE_URL": AI_OFF}},
    {"app": "s5-web", "student": "student-5", "role": "frontend", "port": 3500, "ingress": "external",
     "context": ".", "dockerfile": "student-5/frontend/Dockerfile", "health": "/",
     "secrets": {"homs-secret": "HOMS_SECRET_KEY"},  # pragma: allowlist secret
     "env": {"FRONTEND_PORT": "3500", "STAFF_SHIFT_API_URL": "{s5-api}", "STAFF_SHIFT_API_TIMEOUT": "15"}},

    # --- shared homepage ----------------------------------------------------
    {"app": "homs-home", "student": "shared", "role": "frontend", "port": 80, "ingress": "external",
     "context": "shared/frontend", "dockerfile": "shared/frontend/Dockerfile", "health": "/",
     "env": {}},
]

ROLE_ORDER = {"database": 0, "backend": 1, "frontend": 2}


class DeployError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Command helpers
# --------------------------------------------------------------------------- #


def log(message: str) -> None:
    print(message, flush=True)


def _tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        hint = {"az": "Install the Azure CLI: https://aka.ms/installazurecli (Windows: winget install Microsoft.AzureCLI)",
                "docker": "Install and start Docker Desktop, or use --build acr"}.get(name, "")
        raise DeployError(f"'{name}' was not found on PATH. {hint}")
    return path


def run(args: List[str], *, capture: bool = True, check: bool = True, secret: bool = False) -> str:
    """Run a command with a fixed argument list (no shell). ``secret`` hides it from the log."""
    shown = " ".join(args[:4]) + (" ..." if len(args) > 4 else "") if secret else " ".join(args)
    log(f"  $ {shown}")
    result = subprocess.run(args, capture_output=capture, text=True, check=False)  # noqa: S603
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() if capture else ""
        raise DeployError(f"command failed ({result.returncode}): {shown}\n{detail[-1500:]}")
    return (result.stdout or "").strip() if capture else ""


def az(*args: str, check: bool = True, secret: bool = False) -> str:
    return run([_tool("az"), *args, "--only-show-errors"], check=check, secret=secret)


def az_json(*args: str, check: bool = True) -> Any:
    out = az(*args, "--output", "json", check=check)
    return json.loads(out) if out else None


def app_exists(name: str) -> bool:
    out = run([_tool("az"), "containerapp", "show", "-n", name, "-g", RESOURCE_GROUP,
               "--query", "name", "--output", "tsv", "--only-show-errors"], check=False)
    return out == name


def acr_name() -> str:
    if ACR_NAME:
        return ACR_NAME
    subscription = az("account", "show", "--query", "id", "--output", "tsv")
    return "homsr2" + re.sub(r"[^a-z0-9]", "", subscription.lower())[:10]


def select(only: Optional[List[str]]) -> List[Dict[str, Any]]:
    if not only:
        chosen = SERVICES
    else:
        wanted = set(only)
        chosen = [s for s in SERVICES if s["student"] in wanted or s["app"] in wanted]
        unknown = wanted - {s["student"] for s in SERVICES} - {s["app"] for s in SERVICES}
        if unknown:
            raise DeployError(f"unknown service(s): {', '.join(sorted(unknown))}")
    return sorted(chosen, key=lambda s: ROLE_ORDER[s["role"]])


def resolve_env(env: Dict[str, str]) -> Dict[str, str]:
    return {k: re.sub(r"\{([a-z0-9-]+)\}", lambda m: f"http://{m.group(1)}", v) for k, v in env.items()}


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def cmd_setup(args: argparse.Namespace) -> None:
    account = az_json("account", "show")
    log(f"Subscription: {account['name']} ({account['id']}) as {account['user']['name']}")
    log("Registering resource providers (first run can take a few minutes)")
    for provider in ("Microsoft.App", "Microsoft.OperationalInsights", "Microsoft.ContainerRegistry"):
        az("provider", "register", "--namespace", provider, "--wait")
    az("extension", "add", "--name", "containerapp", "--upgrade", "--yes")

    log(f"Resource group {RESOURCE_GROUP} in {LOCATION}")
    try:
        az("group", "create", "-n", RESOURCE_GROUP, "-l", LOCATION)
    except DeployError as error:
        if "RequestDisallowedByPolicy" in str(error):
            raise DeployError(f"{LOCATION} is not allowed for this subscription. Try another region, e.g. "
                              "set HOMS_AZURE_LOCATION=southeastasia, and run setup again.") from error
        raise

    registry = acr_name()
    log(f"Container registry {registry}")
    az("acr", "create", "-g", RESOURCE_GROUP, "-n", registry, "--sku", "Basic", "--admin-enabled", "true")

    log(f"Container Apps environment {ENVIRONMENT} (creates a Log Analytics workspace)")
    if run([_tool("az"), "containerapp", "env", "show", "-n", ENVIRONMENT, "-g", RESOURCE_GROUP,
            "--query", "name", "--output", "tsv", "--only-show-errors"], check=False) != ENVIRONMENT:
        az("containerapp", "env", "create", "-n", ENVIRONMENT, "-g", RESOURCE_GROUP, "-l", LOCATION)
    log("Legacy setup complete. Do not use this prototype for Release 2 deployment; use the shell/OIDC path.")


def build_image(service: Dict[str, Any], server: str, registry: str, tag: str, mode: str) -> str:
    image = f"{server}/homs-{service['app']}:{tag}"
    if mode == "skip":
        return image
    dockerfile = str(REPO_ROOT / service["dockerfile"])
    context = str(REPO_ROOT / service["context"])
    if mode == "acr":
        az("acr", "build", "-r", registry, "-t", f"homs-{service['app']}:{tag}", "-f", dockerfile, context)
    else:
        docker = _tool("docker")
        run([docker, "build", "--platform", "linux/amd64", "-f", dockerfile, "-t", image, context], capture=False)
        run([docker, "push", image], capture=False)
    return image


def deploy_app(service: Dict[str, Any], image: str, server: str, user: str, password: str,
               always_on: bool) -> None:
    name = service["app"]
    env = resolve_env(service["env"])
    env_args = [f"{k}={v}" for k, v in env.items()]
    secrets_map = service.get("secrets", {})
    env_args += [f"{var}=secretref:{secret}" for secret, var in secrets_map.items()]
    replicas = ("1", "1") if service["role"] == "database" else (("1" if always_on else "0"), "1")

    if app_exists(name):
        log(f"Updating {name}")
        update = ["containerapp", "update", "-n", name, "-g", RESOURCE_GROUP, "--image", image,
                  "--min-replicas", replicas[0], "--max-replicas", replicas[1]]
        if env_args:
            update += ["--set-env-vars", *env_args]
        az(*update)
    else:
        log(f"Creating {name}")
        create = ["containerapp", "create", "-n", name, "-g", RESOURCE_GROUP, "--environment", ENVIRONMENT,
                  "--image", image, "--target-port", str(service["port"]), "--ingress", service["ingress"],
                  "--registry-server", server, "--registry-username", user, "--registry-password", password,
                  "--cpu", "0.25", "--memory", "0.5Gi",
                  "--min-replicas", replicas[0], "--max-replicas", replicas[1]]
        if secrets_map:
            create += ["--secrets", *[f"{secret}={secrets.token_urlsafe(32)}" for secret in secrets_map]]
        if env_args:
            create += ["--env-vars", *env_args]
        az(*create, secret=True)

    # Apps inside the environment call each other over plain HTTP by app name.
    if service["role"] in ("database", "backend"):
        az("containerapp", "ingress", "update", "-n", name, "-g", RESOURCE_GROUP, "--allow-insecure", "true")


def cmd_deploy(args: argparse.Namespace) -> None:
    services = select(args.only)
    registry = acr_name()
    server = az("acr", "show", "-n", registry, "--query", "loginServer", "--output", "tsv")
    creds = az_json("acr", "credential", "show", "-n", registry)
    user, password = creds["username"], creds["passwords"][0]["value"]
    tag = args.tag or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    if args.build == "docker":
        az("acr", "login", "-n", registry)
    log(f"Deploying {len(services)} app(s) with image tag {tag}")
    for service in services:
        log(f"\n== {service['app']} ({service['student']} {service['role']})")
        image = build_image(service, server, registry, tag, args.build)
        deploy_app(service, image, server, user, password, args.always_on)
    log("")
    cmd_status(args)


def app_urls() -> Dict[str, Dict[str, Any]]:
    apps = az_json("containerapp", "list", "-g", RESOURCE_GROUP, "--query",
                   "[].{name:name, fqdn:properties.configuration.ingress.fqdn, "
                   "external:properties.configuration.ingress.external, "
                   "running:properties.runningStatus, image:properties.template.containers[0].image}") or []
    return {a["name"]: a for a in apps}


def cmd_status(args: argparse.Namespace) -> None:
    apps = app_urls()
    log(f"{'app':10} {'access':9} url")
    for service in SERVICES:
        app = apps.get(service["app"])
        if not app:
            log(f"{service['app']:10} {'-':9} not deployed")
            continue
        access = "public" if app.get("external") else "internal"
        url = f"https://{app['fqdn']}" if app.get("external") else f"http://{service['app']} (inside the environment)"
        log(f"{service['app']:10} {access:9} {url}")


def _get(url: str, timeout: float = 30.0) -> Dict[str, Any]:
    if not url.startswith("https://"):
        raise DeployError(f"refusing non-HTTPS URL {url}")
    started = time.monotonic()
    request = urllib.request.Request(url, headers={"Accept": "application/json, text/html"})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - https URLs from Azure
            status, body = response.status, response.read(4000).decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        status, body = error.code, error.read(4000).decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return {"url": url, "ok": False, "status": None, "error": str(error),
                "ms": round((time.monotonic() - started) * 1000)}
    return {"url": url, "ok": 200 <= status < 400, "status": status, "body": body[:300],
            "ms": round((time.monotonic() - started) * 1000)}


def cmd_smoke(args: argparse.Namespace) -> None:
    """Check every public app (the first call may wake an app that scaled to zero)."""
    apps = app_urls()
    results = []
    for service in select(args.only):
        app = apps.get(service["app"])
        if not app or not app.get("external"):
            continue
        url = f"https://{app['fqdn']}{service.get('health', '/')}"
        result = _get(url, timeout=60)
        if not result["ok"]:
            time.sleep(10)
            result = _get(url, timeout=60)
        result.update(app=service["app"], student=service["student"], role=service["role"])
        log(f"{'PASS' if result['ok'] else 'FAIL'} {service['app']:10} {result['status']} {result['ms']}ms {url}")
        results.append(result)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = EVIDENCE_DIR / f"{stamp}-smoke.json"
    path.write_text(json.dumps({"resource_group": RESOURCE_GROUP, "location": LOCATION, "checked_at": stamp,
                                "results": results}, indent=2) + "\n", encoding="utf-8")
    failed = [r["app"] for r in results if not r["ok"]]
    log(f"\n{len(results) - len(failed)}/{len(results)} public apps answered. Evidence: {path}")
    if failed:
        raise DeployError(f"not answering: {', '.join(failed)}")


def cmd_probes(args: argparse.Namespace) -> None:
    """Write the probe file for: agentic_loop.py --mode cloud --probes <file>."""
    apps = app_urls()
    url = {name: f"https://{a['fqdn']}" for name, a in apps.items() if a.get("external")}
    if "s4-api" not in url:
        raise DeployError("deploy the apps first (student-4 is used for the CRUD probes)")
    probes: List[Dict[str, Any]] = []
    for service in SERVICES:
        if service["app"] in url and service["role"] in ("frontend", "backend"):
            probes.append({"id": service["app"].upper().replace("-", "_"), "kind": "core",
                           "description": f"{service['student']} {service['role']} answers",
                           "url": url[service["app"]] + service.get("health", "/"), "expect_status": [200]})
    s4 = url["s4-api"]
    probes += [
        {"id": "CR1", "kind": "crud", "method": "POST", "description": "Create a bed in the cloud database",
         "url": f"{s4}/api/beds", "body": {"room_id": 1, "bed_number": "CLOUD-TEST-01"},
         "expect_status": [201], "save": {"bed_id": "data.bed_id"}},
        {"id": "CR2", "kind": "crud", "description": "Read it back",
         "url": f"{s4}/api/beds/{{bed_id}}", "expect_status": [200],
         "expect_json": {"data.bed_number": "CLOUD-TEST-01"}},
        {"id": "CR3", "kind": "crud", "method": "PUT", "description": "Update it",
         "url": f"{s4}/api/beds/{{bed_id}}", "body": {"status": "maintenance"},
         "expect_status": [200], "expect_json": {"data.status": "maintenance"}},
        {"id": "CR4", "kind": "crud", "method": "DELETE", "description": "Delete it",
         "url": f"{s4}/api/beds/{{bed_id}}", "expect_status": [200, 204]},
        {"id": "AI1", "kind": "ai_disabled", "service": "ai-mode", "description": "No Ollama in the cloud",
         "url": f"{s4}/health", "expect_json": {"data.ai.ok": False}},
        {"id": "AI2", "kind": "ai_disabled", "service": "mcp",
         "url": f"{s4}/api/mcp/status", "expect_json": {"data.enabled": False}},
        {"id": "AI3", "kind": "ai_disabled", "service": "rag",
         "url": f"{s4}/api/rag/status", "expect_json": {"data.enabled": False}},
        {"id": "AI4", "kind": "ai_disabled", "service": "multi-agent",
         "url": f"{s4}/api/multi-agent/status", "expect_json": {"data.enabled": False}},
    ]
    spec = {"platform": "azure", "environment": f"{ENVIRONMENT} ({RESOURCE_GROUP}, {LOCATION})",
            "base_urls": url, "probes": probes}
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = Path(args.out) if args.out else EVIDENCE_DIR / "cloud-probes.json"
    path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    log(f"Wrote {len(probes)} probes to {path}")
    log("Run: python ai-services/agentic-loop/agentic_loop.py --mode cloud "
        f"--probes {path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path} --allow-writes")


def cmd_teardown(args: argparse.Namespace) -> None:
    if not args.yes:
        raise DeployError(f"this deletes resource group {RESOURCE_GROUP} and every app in it; rerun with --yes")
    az("group", "delete", "-n", RESOURCE_GROUP, "--yes", "--no-wait")
    log(f"Deleting {RESOURCE_GROUP} in the background. Costs stop once it is gone.")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deploy the integrated HOMS application to Azure Container Apps.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="create the shared resource group, registry and environment (once)")
    deploy = sub.add_parser("deploy", help="build, push and deploy apps")
    deploy.add_argument("--only", nargs="+", help="student-N and/or app names, e.g. --only student-4")
    deploy.add_argument("--tag", help="image tag (default: UTC timestamp; CI passes the commit SHA)")
    deploy.add_argument("--build", choices=("docker", "acr", "skip"), default="docker")
    deploy.add_argument("--always-on", action="store_true",
                        help="keep one replica of every app running (no cold starts; costs more)")
    sub.add_parser("status", help="print the URL of every app")
    smoke = sub.add_parser("smoke", help="check every public app and save evidence")
    smoke.add_argument("--only", nargs="+")
    probes = sub.add_parser("probes", help="write the probe file for the Cloud Review mode")
    probes.add_argument("--out")
    teardown = sub.add_parser("teardown", help="delete the whole resource group")
    teardown.add_argument("--yes", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    print(
        "WARNING: azure_deploy.py is deprecated and is not the supported R2 "
        "deployment path. Use scripts/deploy/deploy-student.sh.",
        file=sys.stderr,
    )
    args = parse_args(argv)
    handler = {"setup": cmd_setup, "deploy": cmd_deploy, "status": cmd_status, "smoke": cmd_smoke,
               "probes": cmd_probes, "teardown": cmd_teardown}[args.command]
    try:
        handler(args)
    except DeployError as error:
        print(f"\nERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
