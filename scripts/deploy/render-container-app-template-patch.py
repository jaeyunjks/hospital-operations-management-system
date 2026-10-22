#!/usr/bin/env python3
"""Extract a Container Apps ARM template patch from rendered app JSON.

The patch intentionally excludes identity, registry, ingress and environment
storage configuration. It is used for Student 5 updates after the database has
been quiesced, avoiding the Azure CLI full-YAML identity lookup failure while
still applying image, environment, volume, scale and probe changes atomically.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("config", type=Path)
    return result


def main() -> int:
    arguments = parser().parse_args()
    document = json.loads(arguments.config.read_text(encoding="utf-8"))
    template = document["properties"]["template"]
    template.pop("revisionSuffix", None)
    print(json.dumps({"properties": {"template": template}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
