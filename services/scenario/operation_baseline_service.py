from __future__ import annotations

import hashlib
import json
import difflib
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from baseline_models import OperationBaseline, OperationBaselineSourceSnapshot
from config import settings
from repositories.operation_baseline_repository import OperationBaselineRepository
from services.flow.endpoint_flow_service import EndpointFlowService
from services.scenario.scenario_baseline_service import ScenarioBaselineService
from services.scenario.scenario_service import ScenarioService


class OperationBaselineService(ScenarioBaselineService):
    """Operation-level baseline/version manager.

    Key = selected project + HTTP method + endpoint.  Business scenarios are
    test cases under that operation.  A click cannot create V2/V3 unless the
    current relevant source/flow fingerprint differs from the active version.
    """

    def __init__(self):
        super().__init__()
        self.operation_repository = OperationBaselineRepository()

    @staticmethod
    def _normalise_endpoint(endpoint: str) -> str:
        value = "/" + "/".join(part for part in str(endpoint or "").strip().split("/") if part)
        return value if value != "/" else "/"

    def _project_path(self) -> str:
        return str(Path(settings.JAVA_PROJECT_PATH).resolve())

    def _operation_key(self, method: str, endpoint: str) -> str:
        return f"{method.upper().strip()} {self._normalise_endpoint(endpoint)}"

    def _operation_scenarios(self, db: Session, method: str, endpoint: str):
        method = method.upper().strip()
        endpoint = self._normalise_endpoint(endpoint)
        return [
            row for row in ScenarioService().get_all_for_active_project(db)
            if str(row.http_method or "").upper() == method
            and self._normalise_endpoint(row.endpoint) == endpoint
        ]

    @staticmethod
    def _safe_json(value):
        if value is None:
            return None
        if isinstance(value, (dict, list, int, float, bool)):
            return value
        text = str(value).strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            return text

    def _scenario_contracts(self, scenarios) -> list[dict]:
        contracts = []
        for scenario in scenarios:
            contracts.append({
                "scenario_id": scenario.id,
                "scenario_code": scenario.scenario_code,
                "scenario_name": scenario.scenario_name,
                "request_json": self._safe_json(scenario.request_json),
                "expected_response_json": self._safe_json(scenario.expected_response_json),
                "expected_db_effect": scenario.expected_db_effect,
            })
        return contracts

    @staticmethod
    def _stable_hash(value: Any) -> str:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _current_state(self, method: str, endpoint: str) -> dict:
        flow = EndpointFlowService().analyze_endpoint(method, endpoint)
        class_names = self._flow_classes(flow)
        project_path = Path(settings.JAVA_PROJECT_PATH).resolve()
        snapshot = self._read_relevant_java_sources(project_path, class_names)
        code_fingerprint = self._stable_hash(snapshot)
        flow_fingerprint = self._stable_hash(flow)
        combined_fingerprint = self._stable_hash({
            "code": code_fingerprint,
            "flow": flow_fingerprint,
        })
        return {
            "flow": flow,
            "snapshot": snapshot,
            "code_fingerprint": code_fingerprint,
            "flow_fingerprint": flow_fingerprint,
            "combined_fingerprint": combined_fingerprint,
        }

    def _migrate_legacy_if_needed(self, db: Session, method: str, endpoint: str, scenarios):
        project_path = self._project_path()
        existing = self.operation_repository.find_active(db, project_path, method, endpoint)
        if existing or not scenarios:
            return existing

        legacy_rows = []
        for scenario in scenarios:
            legacy = self.baseline_repository.find_active(db, scenario.id)
            if legacy:
                legacy_rows.append(legacy)
        if not legacy_rows:
            return None

        # Consolidate old scenario-level cards into one operation baseline.
        # Preserve the highest old version number as the starting operation
        # version so existing demos do not unexpectedly move backwards.
        state = self._current_state(method, endpoint)
        version = max(int(row.baseline_version or 1) for row in legacy_rows)
        row = OperationBaseline(
            project_path=project_path,
            operation_key=self._operation_key(method, endpoint),
            http_method=method,
            endpoint=endpoint,
            baseline_version=version,
            scenario_codes=[scenario.scenario_code for scenario in scenarios],
            scenario_contracts=self._scenario_contracts(scenarios),
            endpoint_flow=state["flow"],
            code_fingerprint=state["code_fingerprint"],
            flow_fingerprint=state["flow_fingerprint"],
            combined_fingerprint=state["combined_fingerprint"],
            is_active=True,
        )
        created = self.operation_repository.create(db, row)
        snapshot = OperationBaselineSourceSnapshot(
            baseline_id=created.id,
            source_snapshot=state["snapshot"],
            source_changes=[],
            git_diff=None,
        )
        self.operation_repository.create_snapshot(db, snapshot)
        return created

    def get_overview(self, db: Session, http_method: str | None = None):
        scenarios = ScenarioService().get_all_for_active_project(db)
        grouped: dict[tuple[str, str], list] = {}
        for scenario in scenarios:
            method = str(scenario.http_method or "").upper()
            endpoint = self._normalise_endpoint(scenario.endpoint)
            if http_method and method != http_method.upper().strip():
                continue
            grouped.setdefault((method, endpoint), []).append(scenario)

        project_path = self._project_path()
        result = []
        for (method, endpoint), rows in sorted(grouped.items()):
            latest = self.operation_repository.find_active(db, project_path, method, endpoint)
            if latest is None:
                try:
                    latest = self._migrate_legacy_if_needed(db, method, endpoint, rows)
                except Exception:
                    db.rollback()
                    latest = None
            capture_allowed = latest is None
            change_status = "NO_BASELINE" if latest is None else "UNCHANGED"
            current_fingerprint = None
            try:
                state = self._current_state(method, endpoint)
                current_fingerprint = state["combined_fingerprint"]
                if latest is not None and current_fingerprint != latest.combined_fingerprint:
                    capture_allowed = True
                    change_status = "CODE_CHANGED"
            except Exception:
                # Overview must stay usable even when one endpoint cannot be traced.
                change_status = "CHECK_ON_CAPTURE" if latest else "NO_BASELINE"
                capture_allowed = latest is None

            result.append({
                "operation_key": self._operation_key(method, endpoint),
                "http_method": method,
                "endpoint": endpoint,
                "scenario_count": len(rows),
                "scenario_codes": [row.scenario_code for row in rows],
                "scenario_ids": [row.id for row in rows],
                "baseline_captured": latest is not None,
                "active_baseline_version": latest.baseline_version if latest else None,
                "active_baseline_id": latest.id if latest else None,
                "history_count": len(self.operation_repository.find_history(db, project_path, method, endpoint)),
                "captured_at": latest.created_at.isoformat() if latest else None,
                "capture_allowed": capture_allowed,
                "change_status": change_status,
                "current_fingerprint": current_fingerprint,
            })
        return result

    def capture(self, db: Session, http_method: str, endpoint: str):
        method = http_method.upper().strip()
        endpoint = self._normalise_endpoint(endpoint)
        project_path = self._project_path()
        scenarios = self._operation_scenarios(db, method, endpoint)
        if not scenarios:
            raise HTTPException(status_code=404, detail="No registered scenarios found for this operation")

        state = self._current_state(method, endpoint)
        latest = self.operation_repository.find_latest(db, project_path, method, endpoint)

        if latest and latest.combined_fingerprint == state["combined_fingerprint"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "NO_RELEVANT_CODE_CHANGE",
                    "message": f"No relevant code or flow change detected. V{latest.baseline_version} remains the active baseline.",
                    "active_version": latest.baseline_version,
                    "operation": self._operation_key(method, endpoint),
                },
            )

        next_version = (latest.baseline_version + 1) if latest else 1
        self.operation_repository.deactivate_existing(db, project_path, method, endpoint)

        row = OperationBaseline(
            project_path=project_path,
            operation_key=self._operation_key(method, endpoint),
            http_method=method,
            endpoint=endpoint,
            baseline_version=next_version,
            scenario_codes=[scenario.scenario_code for scenario in scenarios],
            scenario_contracts=self._scenario_contracts(scenarios),
            endpoint_flow=state["flow"],
            code_fingerprint=state["code_fingerprint"],
            flow_fingerprint=state["flow_fingerprint"],
            combined_fingerprint=state["combined_fingerprint"],
            is_active=True,
        )
        created = self.operation_repository.create(db, row)

        git_diff = self._current_git_diff(Path(project_path))
        snapshot = OperationBaselineSourceSnapshot(
            baseline_id=created.id,
            source_snapshot=state["snapshot"],
            source_changes=self._classify_git_diff(git_diff),
            git_diff=git_diff or None,
        )
        self.operation_repository.create_snapshot(db, snapshot)

        return {
            "status": "BASELINE_CREATED",
            "operation": created.operation_key,
            "baseline_version": created.baseline_version,
            "baseline_id": created.id,
            "scenario_codes": created.scenario_codes,
            "message": f"V{created.baseline_version} captured for {created.operation_key}.",
        }

    def history(self, db: Session, http_method: str, endpoint: str):
        method = http_method.upper().strip()
        endpoint = self._normalise_endpoint(endpoint)
        rows = self.operation_repository.find_history(db, self._project_path(), method, endpoint)
        return [self._to_dict(row) for row in rows]

    def compare(self, db: Session, http_method: str, endpoint: str, from_version: int, to_version: int):
        method = http_method.upper().strip()
        endpoint = self._normalise_endpoint(endpoint)
        project_path = self._project_path()
        old = self.operation_repository.find_by_version(db, project_path, method, endpoint, from_version)
        new = self.operation_repository.find_by_version(db, project_path, method, endpoint, to_version)
        if not old or not new:
            raise HTTPException(
                status_code=404,
                detail="Both versions must belong to the same selected operation and project.",
            )

        old_snapshot_row = self.operation_repository.find_snapshot(db, old.id)
        new_snapshot_row = self.operation_repository.find_snapshot(db, new.id)
        old_sources = (old_snapshot_row.source_snapshot or {}) if old_snapshot_row else {}
        new_sources = (new_snapshot_row.source_snapshot or {}) if new_snapshot_row else {}
        source_diff = self._snapshot_diff(old_sources, new_sources)

        old_methods = self._flow_methods(old.endpoint_flow)
        new_methods = self._flow_methods(new.endpoint_flow)
        old_contracts = {c.get("scenario_code"): c for c in (old.scenario_contracts or [])}
        new_contracts = {c.get("scenario_code"): c for c in (new.scenario_contracts or [])}

        return {
            "operation": self._operation_key(method, endpoint),
            "http_method": method,
            "endpoint": endpoint,
            "from_version": from_version,
            "to_version": to_version,
            "source_comparison": source_diff,
            "flow_changed": old.flow_fingerprint != new.flow_fingerprint,
            "added_methods": sorted(new_methods - old_methods),
            "removed_methods": sorted(old_methods - new_methods),
            "scenario_changes": {
                "added": sorted(set(new_contracts) - set(old_contracts)),
                "removed": sorted(set(old_contracts) - set(new_contracts)),
                "changed": sorted(
                    code for code in set(old_contracts) & set(new_contracts)
                    if old_contracts[code] != new_contracts[code]
                ),
            },
            "changed": old.combined_fingerprint != new.combined_fingerprint,
        }

    @staticmethod
    def _snapshot_diff(old_sources: dict, new_sources: dict) -> dict:
        old_files = set(old_sources)
        new_files = set(new_sources)
        added_files = sorted(new_files - old_files)
        removed_files = sorted(old_files - new_files)
        modified_files = sorted(
            path for path in old_files & new_files
            if old_sources.get(path) != new_sources.get(path)
        )
        changes = []
        file_diffs = []

        for path in added_files:
            changes.append({"change_type": "FILE_ADDED", "file_path": path})
        for path in removed_files:
            changes.append({"change_type": "FILE_REMOVED", "file_path": path})

        for path in modified_files:
            changes.append({"change_type": "FILE_MODIFIED", "file_path": path})
            old_lines = str(old_sources.get(path) or "").splitlines()
            new_lines = str(new_sources.get(path) or "").splitlines()
            diff_lines = list(difflib.unified_diff(
                old_lines, new_lines,
                fromfile=f"VOLD/{path}", tofile=f"VNEW/{path}", lineterm=""
            ))
            added = [line[1:] for line in diff_lines if line.startswith("+") and not line.startswith("+++")]
            removed = [line[1:] for line in diff_lines if line.startswith("-") and not line.startswith("---")]
            file_diffs.append({
                "file_path": path,
                "added_lines": added[:100],
                "removed_lines": removed[:100],
                "diff": "\n".join(diff_lines[:300]),
            })

        return {
            "added_files": added_files,
            "removed_files": removed_files,
            "modified_files": modified_files,
            "changed_files": sorted(set(added_files + removed_files + modified_files)),
            "changes": changes,
            "file_diffs": file_diffs,
        }

    @staticmethod
    def _to_dict(row: OperationBaseline) -> dict:
        return {
            "id": row.id,
            "operation_key": row.operation_key,
            "http_method": row.http_method,
            "endpoint": row.endpoint,
            "baseline_version": row.baseline_version,
            "scenario_codes": row.scenario_codes or [],
            "scenario_contracts": row.scenario_contracts or [],
            "endpoint_flow": row.endpoint_flow,
            "is_active": row.is_active,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
