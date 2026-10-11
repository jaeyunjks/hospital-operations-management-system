"""Persistent, collision-safe JSON workflow history."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict


class AuditStore:
    def __init__(self, reports_dir: Path):
        self.reports_dir = reports_dir
        self._reserved: set[str] = set()

    def path_for(self, workflow_id: str) -> Path:
        return self.reports_dir / f"workflow-{workflow_id}.json"

    def create(self, record: Dict[str, Any]) -> Path:
        """Reserve a new workflow file without overwriting any earlier run."""

        self.reports_dir.mkdir(parents=True, exist_ok=True)
        path = self.path_for(record["workflow_id"])
        with path.open("x", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        self._reserved.add(record["workflow_id"])
        return path

    def update(self, record: Dict[str, Any]) -> Path:
        workflow_id = record["workflow_id"]
        path = self.path_for(workflow_id)
        if workflow_id not in self._reserved or not path.is_file():
            raise RuntimeError("audit update attempted before workflow file creation")
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(record, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
        return path
