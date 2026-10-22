#!/usr/bin/env python3
"""Render an Azure Container Apps YAML-compatible JSON document.

JSON is valid YAML and avoids adding a YAML runtime dependency to deployment
scripts. The generated document contains no registry password: apps pull from
ACR through the shared user-assigned managed identity.
"""

from __future__ import annotations

import argparse
import json
import sys


def parse_env(value: str) -> dict[str, str]:
    name, separator, setting = value.partition("=")
    if not separator or not name:
        raise argparse.ArgumentTypeError("environment values must use NAME=VALUE")
    return {"name": name, "value": setting}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--name", required=True)
    result.add_argument("--location", required=True)
    result.add_argument("--environment-id", required=True)
    result.add_argument("--identity-id", required=True)
    result.add_argument("--registry-server", required=True)
    result.add_argument("--image", required=True)
    result.add_argument("--container-name", required=True)
    result.add_argument("--port", required=True, type=int)
    result.add_argument("--ingress", choices=("external", "internal"), required=True)
    result.add_argument("--health-path", default="/health")
    result.add_argument("--min-replicas", type=int, default=1)
    result.add_argument("--max-replicas", type=int, default=1)
    result.add_argument("--cpu", type=float, default=0.25)
    result.add_argument("--memory", default="0.5Gi")
    result.add_argument("--env", action="append", default=[], type=parse_env)
    result.add_argument("--storage-name")
    result.add_argument("--mount-path")
    return result


def main() -> int:
    arguments = parser().parse_args()
    if bool(arguments.storage_name) != bool(arguments.mount_path):
        parser().error("--storage-name and --mount-path must be supplied together")

    container = {
        "name": arguments.container_name,
        "image": arguments.image,
        "env": arguments.env,
        "resources": {"cpu": arguments.cpu, "memory": arguments.memory},
        "probes": [
            {
                "type": "Liveness",
                "httpGet": {
                    "path": arguments.health_path,
                    "port": arguments.port,
                },
                "initialDelaySeconds": 15,
                "periodSeconds": 10,
                "timeoutSeconds": 5,
                "failureThreshold": 6,
            },
            {
                "type": "Readiness",
                "httpGet": {
                    "path": arguments.health_path,
                    "port": arguments.port,
                },
                "initialDelaySeconds": 5,
                "periodSeconds": 5,
                "timeoutSeconds": 5,
                "failureThreshold": 12,
            },
        ],
    }
    volumes: list[dict[str, str]] = []
    if arguments.storage_name:
        container["volumeMounts"] = [
            {"volumeName": "database-files", "mountPath": arguments.mount_path}
        ]
        volumes.append(
            {
                "name": "database-files",
                "storageType": "AzureFile",
                "storageName": arguments.storage_name,
            }
        )

    document = {
        "name": arguments.name,
        "location": arguments.location,
        "identity": {
            "type": "UserAssigned",
            "userAssignedIdentities": {arguments.identity_id: {}},
        },
        "properties": {
            "environmentId": arguments.environment_id,
            "configuration": {
                "activeRevisionsMode": "Single",
                "ingress": {
                    "external": arguments.ingress == "external",
                    "allowInsecure": False,
                    "targetPort": arguments.port,
                    "transport": "Auto",
                },
                "registries": [
                    {
                        "server": arguments.registry_server,
                        "identity": arguments.identity_id,
                    }
                ],
            },
            "template": {
                "containers": [container],
                "scale": {
                    "minReplicas": arguments.min_replicas,
                    "maxReplicas": arguments.max_replicas,
                },
                "volumes": volumes,
            },
        },
    }
    json.dump(document, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
