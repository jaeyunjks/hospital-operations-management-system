"""Persistent, collision-safe JSON workflow history."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Dict, List


class AuditNotFoundError(FileNotFoundError):
    """Raised when a persisted workflow record does not exist."""


class AuditStore:
    def __init__(self, reports_dir: Path):
        self.reports_dir = reports_dir
        self._lock = RLock()

    def path_for(self, workflow_id: str) -> Path:
        return self.reports_dir / f"workflow-{workflow_id}.json"

    def create(self, record: Dict[str, Any]) -> Path:
        """Reserve a new workflow file without overwriting any earlier run."""

        with self._lock:
            self.reports_dir.mkdir(parents=True, exist_ok=True)
            path = self.path_for(record["workflow_id"])
            with path.open("x", encoding="utf-8") as handle:
                json.dump(record, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
        return path

    def read(self, workflow_id: str) -> Dict[str, Any]:
        """Load a workflow from its durable JSON audit record."""

        with self._lock:
            path = self.path_for(workflow_id)
            if not path.is_file():
                raise AuditNotFoundError(f"workflow not found: {workflow_id}")
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise RuntimeError(f"could not read workflow audit: {workflow_id}") from error
            if not isinstance(record, dict) or record.get("workflow_id") != workflow_id:
                raise RuntimeError(f"invalid workflow audit: {workflow_id}")
            return record

    def workflow_ids_with_status(self, status: str) -> list[str]:
        """Return valid persisted workflow IDs currently in one lifecycle state."""

        with self._lock:
            if not self.reports_dir.is_dir():
                return []
            workflow_ids = []
            for path in sorted(self.reports_dir.glob("workflow-*.json")):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                workflow_id = record.get("workflow_id") if isinstance(record, dict) else None
                if (
                    isinstance(workflow_id, str)
                    and record.get("final_status") == status
                    and path == self.path_for(workflow_id)
                ):
                    workflow_ids.append(workflow_id)
            return workflow_ids

    def list_by_evidence_type(
        self,
        evidence_type: str,
        *,
        limit: int,
        evidence_path: str | None = None,
        evidence_value: str | None = None,
    ) -> List[Dict[str, Any]]:
        """Return recent valid audits for one explicitly requested integration.

        Listing is deliberately scoped by ``evidence_type`` so the HTTP layer
        never exposes an unrestricted audit directory. Invalid or unrelated
        files are ignored rather than making history retrieval unavailable.
        """

        with self._lock:
            if not self.reports_dir.is_dir():
                return []
            records = []
            for path in self.reports_dir.glob("workflow-*.json"):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                workflow_id = record.get("workflow_id") if isinstance(record, dict) else None
                evidence = record.get("input_evidence") if isinstance(record, dict) else None
                if (
                    isinstance(workflow_id, str)
                    and isinstance(evidence, dict)
                    and evidence.get("evidence_type") == evidence_type
                    and path == self.path_for(workflow_id)
                ):
                    if evidence_path is not None:
                        current: Any = evidence
                        for key in evidence_path.split("."):
                            if not isinstance(current, dict) or key not in current:
                                current = None
                                break
                            current = current[key]
                        if isinstance(current, (dict, list)) or str(current) != evidence_value:
                            continue
                    records.append(record)
            records.sort(
                key=lambda record: record.get("timestamp", ""), reverse=True
            )
            return records[:limit]

    @staticmethod
    def _write(path: Path, record: Dict[str, Any]) -> None:
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(record, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def update(self, record: Dict[str, Any]) -> Path:
        with self._lock:
            path = self.path_for(record["workflow_id"])
            if not path.is_file():
                raise RuntimeError("audit update attempted before workflow file creation")
            self._write(path, record)
            return path

    def update_existing(
        self,
        workflow_id: str,
        updater: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> tuple[Dict[str, Any], Path]:
        """Atomically load, validate/change, and persist one existing workflow."""

        with self._lock:
            record = self.read(workflow_id)
            updated = updater(record)
            if updated.get("workflow_id") != workflow_id:
                raise RuntimeError("workflow updater changed the workflow ID")
            path = self.path_for(workflow_id)
            self._write(path, updated)
            return updated, path
