import json
import difflib
import re
import subprocess
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from config import settings
from sqlalchemy.orm import Session

from baseline_models import ScenarioBaseline, ScenarioBaselineSourceSnapshot, ScenarioTestBaseline
from repositories.scenario_baseline_repository import (
    ScenarioBaselineRepository
)
from repositories.scenario_repository import ScenarioRepository
from services.scenario.scenario_service import ScenarioService
from services.scenario.source_snapshot_diff import capture_sources, compare_sources, unpack, path_key, EXCLUDED
from services.scenario.baseline_risk_report import build_risk_report


class ScenarioBaselineService:

    def __init__(self):
        self.scenario_repository = ScenarioRepository()
        self.baseline_repository = (
            ScenarioBaselineRepository()
        )

    def capture(
        self,
        db: Session,
        scenario_id: int,
        baseline_name: str,
        successful_response: Any = None,
        endpoint_flow: dict | None = None
    ):

        scenario = (
            self.scenario_repository.find_by_id(
                db,
                scenario_id
            )
        )

        if not scenario:
            raise HTTPException(
                status_code=404,
                detail="Scenario not found"
            )

        name = str(baseline_name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Main Baseline name is required")

        latest = self.baseline_repository.find_latest(db, scenario_id)
        if latest:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "A Main Baseline already exists for this scenario. Add Test Baselines under the existing Main Baseline instead.",
                    "active_version": latest.baseline_version,
                    "baseline_name": latest.baseline_name,
                }
            )

        next_version = 1

        self.baseline_repository.deactivate_existing(
            db,
            scenario_id
        )

        baseline = ScenarioBaseline(
            scenario_id=scenario.id,
            scenario_code=scenario.scenario_code,
            baseline_version=next_version,
            baseline_name=name,
            release_version=1,
            http_method=scenario.http_method,
            endpoint=scenario.endpoint,
            expected_response_json=self._normalize_json(
                scenario.expected_response_json
            ),
            successful_response_json=self._normalize_json(
                successful_response
            ),
            expected_db_effect=scenario.expected_db_effect,
            involved_classes=self._normalize_list(
                scenario.involved_classes
            ),
            endpoint_flow=endpoint_flow,
            is_active=True
        )

        created = self.baseline_repository.create(
            db,
            baseline
        )

        self._capture_source_snapshot(
            db=db,
            baseline=created
        )

        return created

    def create_next_baseline(
        self,
        db: Session,
        scenario_id: int,
        baseline_name: str,
        successful_response: Any = None,
        endpoint_flow: dict | None = None
    ):
        """Create the next intentional Main Baseline version for a scenario.

        The first baseline must be created through ``capture``. This method is only
        for moving an existing scenario from Vn to Vn+1 and rejects an identical
        code/flow snapshot so users cannot accidentally create duplicate versions.
        """
        scenario = self.scenario_repository.find_by_id(db, scenario_id)
        if not scenario:
            raise HTTPException(status_code=404, detail="Scenario not found")

        name = str(baseline_name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Main Baseline name is required")

        latest = self.baseline_repository.find_latest(db, scenario_id)
        if not latest:
            raise HTTPException(
                status_code=409,
                detail="Create the first Main Baseline before creating a new version"
            )

        if not self._code_or_flow_changed(db, scenario, latest, endpoint_flow):
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "No code or flow changes were detected since the current Main Baseline. Keep using the existing baseline for Test Baselines.",
                    "active_version": latest.baseline_version,
                    "baseline_name": latest.baseline_name,
                }
            )

        next_version = int(latest.baseline_version or 0) + 1
        same_release = self.baseline_repository.find_release_versions(db, scenario_id, name)
        release_version = max([int(x.release_version or 0) for x in same_release] + [0]) + 1
        self.baseline_repository.deactivate_existing(db, scenario_id)

        baseline = ScenarioBaseline(
            scenario_id=scenario.id,
            scenario_code=scenario.scenario_code,
            baseline_version=next_version,
            baseline_name=name,
            release_version=release_version,
            http_method=scenario.http_method,
            endpoint=scenario.endpoint,
            expected_response_json=self._normalize_json(scenario.expected_response_json),
            successful_response_json=self._normalize_json(successful_response),
            expected_db_effect=scenario.expected_db_effect,
            involved_classes=self._normalize_list(scenario.involved_classes),
            endpoint_flow=endpoint_flow,
            is_active=True
        )

        created = self.baseline_repository.create(db, baseline)
        self._capture_source_snapshot(db=db, baseline=created)
        return created

    def create_release_version(
        self,
        db: Session,
        scenario_id: int,
        release_name: str,
        successful_response: Any = None,
        endpoint_flow: dict | None = None
    ):
        """Create the next version inside a release, or V1 for a new release.

        baseline_version is the internal monotonically increasing id used by
        source comparison. release_version is what the UI displays (October V1,
        October V2, November V1, ...).
        """
        scenario = self.scenario_repository.find_by_id(db, scenario_id)
        if not scenario:
            raise HTTPException(status_code=404, detail="Scenario not found")

        name = str(release_name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Release name is required")

        latest = self.baseline_repository.find_latest(db, scenario_id)
        next_global_version = int(latest.baseline_version or 0) + 1 if latest else 1
        release_versions = self.baseline_repository.find_release_versions(db, scenario_id, name)
        next_release_version = max([int(x.release_version or 0) for x in release_versions] + [0]) + 1

        self.baseline_repository.deactivate_existing(db, scenario_id)
        baseline = ScenarioBaseline(
            scenario_id=scenario.id,
            scenario_code=scenario.scenario_code,
            baseline_version=next_global_version,
            baseline_name=name,
            release_version=next_release_version,
            http_method=scenario.http_method,
            endpoint=scenario.endpoint,
            expected_response_json=self._normalize_json(scenario.expected_response_json),
            successful_response_json=self._normalize_json(successful_response),
            expected_db_effect=scenario.expected_db_effect,
            involved_classes=self._normalize_list(scenario.involved_classes),
            endpoint_flow=endpoint_flow,
            is_active=True
        )
        created = self.baseline_repository.create(db, baseline)
        self._capture_source_snapshot(db=db, baseline=created)
        return created

    def add_baseline(
        self,
        db: Session,
        scenario_id: int,
        baseline_name: str | None = None,
        request_json: Any = None,
        expected_response: Any = None,
        actual_response: Any = None,
        expected_db_effect: str | None = None,
        jira_ids: list[str] | None = None,
        endpoint_flow: dict | None = None
    ):
        """Legacy combined baseline endpoint. Main and Test Baselines are now separate."""
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Main Baseline and Test Baseline are separate. Create the Main Baseline first, then add Test Baselines under it.",
                "main_baseline_endpoint": f"/api/scenario-baselines/capture/{scenario_id}",
                "test_baseline_endpoint": f"/api/scenario-baselines/testing/{scenario_id}",
            }
        )

    @staticmethod
    def _testing_payload_signature(request_json, expected_response, actual_response, expected_db_effect, jira_ids, status):
        payload = {
            "request_json": request_json,
            "expected_response": expected_response,
            "actual_response": actual_response,
            "expected_db_effect": expected_db_effect or None,
            "jira_ids": sorted(jira_ids or []),
            "status": status,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)

    def _stored_testing_payload_signature(self, record):
        if not record:
            return ""
        return self._testing_payload_signature(
            record.request_json, record.expected_response_json, record.actual_response_json,
            record.expected_db_effect, record.jira_ids or [], record.status
        )

    def _code_or_flow_changed(self, db: Session, scenario, latest: ScenarioBaseline, endpoint_flow: dict | None) -> bool:
        latest_snapshot = self.baseline_repository.find_source_snapshot(db, latest.id)
        if not latest_snapshot or latest_snapshot.source_snapshot is None:
            return True

        class_names = set(self._normalize_list(scenario.involved_classes))
        class_names.update(self._flow_classes(endpoint_flow))
        class_names.update(self._flow_classes(latest.endpoint_flow))
        project_path = Path(settings.JAVA_PROJECT_PATH).resolve()
        current_source = self._read_relevant_java_sources(
            project_path=project_path, class_names=class_names
        )
        previous, _ = unpack(latest_snapshot.source_snapshot)
        if latest_snapshot.source_snapshot.get("format_version") != 2:
            current_source = {path: text.replace("\r\n", "\n").replace("\r", "\n")
                              for path, text in current_source.items()}
        previous = {path: content for path, content in previous.items()
                    if not class_names or Path(path).stem in {str(name).split('.')[-1] for name in class_names}}
        source_changed = current_source != previous
        flow_changed = self._flow_signature(endpoint_flow) != self._flow_signature(latest.endpoint_flow)
        return source_changed or flow_changed

    def create_test_baseline(
        self,
        db: Session,
        scenario_id: int,
        baseline_name: str,
        baseline_id: int | None = None,
        request_json: Any = None,
        expected_response: Any = None,
        actual_response: Any = None,
        expected_db_effect: str | None = None,
        jira_ids: list[str] | None = None
    ):
        scenario = self.scenario_repository.find_by_id(db, scenario_id)
        if not scenario:
            raise HTTPException(status_code=404, detail="Scenario not found")

        name = str(baseline_name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Testing baseline name is required")

        target_code_baseline = (
            self.baseline_repository.find_by_id(db, scenario_id, baseline_id)
            if baseline_id is not None
            else self.baseline_repository.find_active(db, scenario_id)
        )
        if not target_code_baseline:
            raise HTTPException(
                status_code=409,
                detail="Select a valid Release/Version before adding a Test Baseline"
            )

        existing = self.baseline_repository.find_test_baseline_by_name(
            db, scenario_id, name, int(target_code_baseline.baseline_version)
        )
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Test Scenario '{name}' already exists for the selected version"
            )
        normalized_expected = self._normalize_json(expected_response)
        normalized_actual = self._normalize_json(actual_response)

        if actual_response is None:
            status = "NOT_RUN"
        elif normalized_expected == normalized_actual:
            status = "PASS"
        else:
            status = "FAIL"

        record = ScenarioTestBaseline(
            scenario_id=scenario.id,
            baseline_name=name,
            http_method=scenario.http_method,
            endpoint=scenario.endpoint,
            request_json=self._normalize_json(request_json),
            expected_response_json=normalized_expected,
            actual_response_json=normalized_actual,
            expected_db_effect=expected_db_effect or scenario.expected_db_effect,
            jira_ids=self._normalize_jira_ids(jira_ids),
            status=status,
            code_baseline_version=target_code_baseline.baseline_version,
            baseline_id=target_code_baseline.id
        )
        return self.baseline_repository.create_test_baseline(db, record)

    @staticmethod
    def _normalize_jira_ids(jira_ids: list[str] | None) -> list[str]:
        result = []
        seen = set()
        for value in jira_ids or []:
            jira_id = str(value or "").strip().upper()
            if not jira_id or jira_id in seen:
                continue
            seen.add(jira_id)
            result.append(jira_id)
        return result

    def get_test_baselines(self, db: Session, scenario_id: int):
        scenario = self.scenario_repository.find_by_id(db, scenario_id)
        if not scenario:
            raise HTTPException(status_code=404, detail="Scenario not found")
        return self.baseline_repository.find_test_baselines(db, scenario_id)

    def get_jira_coverage(self, db: Session, jira_id: str):
        jira_id = str(jira_id or "").strip().upper()
        if not jira_id:
            raise HTTPException(status_code=400, detail="JIRA ID is required")

        scenarios = ScenarioService().get_all_for_active_project(db)
        scenario_by_id = {scenario.id: scenario for scenario in scenarios}
        if not scenario_by_id:
            return {
                "jira_id": jira_id,
                "covered_count": 0,
                "scenario_count": 0,
                "items": [],
            }

        items = []
        for scenario in scenarios:
            for baseline in self.baseline_repository.find_test_baselines(db, scenario.id):
                baseline_jiras = self._normalize_jira_ids(baseline.jira_ids or [])
                if jira_id not in baseline_jiras:
                    continue

                code_baseline = (
                    self.baseline_repository.find_by_id(db, scenario.id, baseline.baseline_id)
                    if baseline.baseline_id
                    else None
                )
                release_name = (
                    code_baseline.baseline_name
                    if code_baseline and code_baseline.baseline_name
                    else "Release"
                )
                release_version = (
                    code_baseline.release_version
                    if code_baseline and code_baseline.release_version
                    else baseline.code_baseline_version
                )

                items.append({
                    "jira_id": jira_id,
                    "scenario_id": scenario.id,
                    "scenario_code": scenario.scenario_code,
                    "scenario_name": scenario.scenario_name,
                    "http_method": scenario.http_method,
                    "endpoint": scenario.endpoint,
                    "testing_baseline_id": baseline.id,
                    "testing_baseline_name": baseline.baseline_name,
                    "status": baseline.status,
                    "release_name": release_name,
                    "release_version": release_version,
                    "code_baseline_version": baseline.code_baseline_version,
                    "related_jiras": baseline_jiras,
                    "created_at": baseline.created_at.isoformat() if baseline.created_at else None,
                    "has_request": baseline.request_json is not None,
                    "has_expected_response": baseline.expected_response_json is not None,
                    "has_actual_response": baseline.actual_response_json is not None,
                    "has_db_effect": bool(baseline.expected_db_effect),
                })

        items.sort(
            key=lambda item: (
                item["scenario_code"],
                item["release_name"] or "",
                int(item["release_version"] or 0),
                item["testing_baseline_name"] or "",
            )
        )
        return {
            "jira_id": jira_id,
            "covered_count": len(items),
            "scenario_count": len({item["scenario_id"] for item in items}),
            "items": items,
        }

    def get_latest(
        self,
        db: Session,
        scenario_id: int
    ):

        scenario = (
            self.scenario_repository.find_by_id(
                db,
                scenario_id
            )
        )

        if not scenario:
            raise HTTPException(
                status_code=404,
                detail="Scenario not found"
            )

        baseline = (
            self.baseline_repository.find_active(
                db,
                scenario_id
            )
        )

        if not baseline:
            raise HTTPException(
                status_code=404,
                detail="No baseline found for scenario"
            )

        return baseline

    def get_history(
        self,
        db: Session,
        scenario_id: int
    ):

        scenario = (
            self.scenario_repository.find_by_id(
                db,
                scenario_id
            )
        )

        if not scenario:
            raise HTTPException(
                status_code=404,
                detail="Scenario not found"
            )

        return (
            self.baseline_repository
            .find_all_for_scenario(
                db,
                scenario_id
            )
        )


    def get_overview(
        self,
        db: Session
    ):
        scenarios = ScenarioService().get_all_for_active_project(db)
        active = {
            baseline.scenario_id: baseline
            for baseline in self.baseline_repository.find_all_active(db)
        }
        all_baselines = self.baseline_repository.find_all(db)
        history_counts = {}
        for baseline in all_baselines:
            history_counts[baseline.scenario_id] = history_counts.get(baseline.scenario_id, 0) + 1

        testing_counts = {}
        for scenario in scenarios:
            testing_counts[scenario.id] = len(
                self.baseline_repository.find_test_baselines(db, scenario.id)
            )

        result = []
        for scenario in scenarios:
            baseline = active.get(scenario.id)
            result.append({
                "scenario_id": scenario.id,
                "scenario_code": scenario.scenario_code,
                "scenario_name": scenario.scenario_name,
                "http_method": scenario.http_method,
                "endpoint": scenario.endpoint,
                "scenario_status": scenario.status,
                "baseline_captured": baseline is not None,
                "active_baseline_version": baseline.baseline_version if baseline else None,
                "active_release_version": (baseline.release_version or baseline.baseline_version) if baseline else None,
                "active_baseline_name": baseline.baseline_name if baseline else None,
                "active_baseline_id": baseline.id if baseline else None,
                "flow_stored": bool(baseline and baseline.endpoint_flow),
                "history_count": history_counts.get(scenario.id, 0),
                "testing_baseline_count": testing_counts.get(scenario.id, 0),
                "captured_at": baseline.created_at.isoformat() if baseline else None,
            })
        return result


    def compare_versions(
        self,
        db: Session,
        scenario_id: int,
        from_version: int,
        to_version: int
    ):
        scenario = self.scenario_repository.find_by_id(db, scenario_id)
        if not scenario:
            raise HTTPException(status_code=404, detail="Scenario not found")

        old = self.baseline_repository.find_by_version(db, scenario_id, from_version)
        new = self.baseline_repository.find_by_version(db, scenario_id, to_version)
        if not old or not new:
            raise HTTPException(status_code=404, detail="One or both baseline versions were not found")

        old_methods = self._flow_methods(old.endpoint_flow)
        new_methods = self._flow_methods(new.endpoint_flow)
        old_classes = set(old.involved_classes or [])
        new_classes = set(new.involved_classes or [])
        added_methods = sorted(new_methods - old_methods)
        removed_methods = sorted(old_methods - new_methods)
        added_classes = sorted(new_classes - old_classes)
        removed_classes = sorted(old_classes - new_classes)
        response_changes = self._json_diff(old.successful_response_json, new.successful_response_json)
        expected_response_changes = self._json_diff(old.expected_response_json, new.expected_response_json)
        source_comparison = self._compare_source_snapshots(
            db=db,
            old_baseline=old,
            new_baseline=new
        )
        testing_comparison = self._compare_testing_baselines(
            db=db, scenario_id=scenario_id,
            from_version=from_version, to_version=to_version
        )

        endpoint_changed = old.endpoint != new.endpoint or old.http_method != new.http_method
        db_effect_changed = old.expected_db_effect != new.expected_db_effect

        response_change_count = sum(
            len(response_changes.get(key) or [])
            for key in ("added_attributes", "removed_attributes", "changed_attributes")
        )
        expected_change_count = sum(
            len(expected_response_changes.get(key) or [])
            for key in ("added_attributes", "removed_attributes", "changed_attributes")
        )
        source_change_count = len(source_comparison.get("changes") or [])
        changed_file_count = len(source_comparison.get("changed_files") or [])
        risk_report = build_risk_report(
            source_comparison, endpoint_changed, db_effect_changed,
            added_methods, removed_methods,
            self.baseline_repository.find_test_baselines_for_code_version(db, scenario_id, to_version),
            scenario.scenario_code,
            old_classes | new_classes | self._flow_classes(old.endpoint_flow) | self._flow_classes(new.endpoint_flow),
        )

        return {
            "risk_report": risk_report,
            "scenario_id": scenario_id,
            "scenario_code": scenario.scenario_code,
            "from_version": from_version,
            "to_version": to_version,
            "from_release_name": old.baseline_name,
            "to_release_name": new.baseline_name,
            "from_release_version": old.release_version or old.baseline_version,
            "to_release_version": new.release_version or new.baseline_version,
            "endpoint_changed": endpoint_changed,
            "from_endpoint": f"{old.http_method} {old.endpoint}",
            "to_endpoint": f"{new.http_method} {new.endpoint}",
            "flow_changed": bool(added_methods or removed_methods),
            "added_methods": added_methods,
            "removed_methods": removed_methods,
            "unchanged_methods": sorted(old_methods & new_methods),
            "added_classes": added_classes,
            "removed_classes": removed_classes,
            "response_changes": response_changes,
            "expected_response_changes": expected_response_changes,
            "db_effect_changed": db_effect_changed,
            "from_db_effect": old.expected_db_effect,
            "to_db_effect": new.expected_db_effect,
            "source_comparison": source_comparison,
            "testing_comparison": testing_comparison,
            "change_summary": {
                "changed_source_files": changed_file_count,
                "classified_source_changes": source_change_count,
                "flow_methods_added": len(added_methods),
                "flow_methods_removed": len(removed_methods),
                "classes_added": len(added_classes),
                "classes_removed": len(removed_classes),
                "response_attribute_changes": response_change_count,
                "expected_attribute_changes": expected_change_count,
                "endpoint_changed": endpoint_changed,
                "db_effect_changed": db_effect_changed,
            },
            "scenario_impact": {
                "scenario_code": scenario.scenario_code,
                "endpoint": f"{new.http_method} {new.endpoint}",
                "version_transition": f"{old.baseline_name or 'Release'} V{old.release_version or old.baseline_version} → {new.baseline_name or 'Release'} V{new.release_version or new.baseline_version}",
                "changed": bool(
                    endpoint_changed
                    or db_effect_changed
                    or added_methods
                    or removed_methods
                    or added_classes
                    or removed_classes
                    or response_change_count
                    or expected_change_count
                    or source_change_count
                    or changed_file_count
                    or testing_comparison.get("changed")
                ),
            },
        }

    def _compare_testing_baselines(self, db: Session, scenario_id: int, from_version: int, to_version: int) -> dict:
        old_items = self.baseline_repository.find_test_baselines_for_code_version(db, scenario_id, from_version)
        new_items = self.baseline_repository.find_test_baselines_for_code_version(db, scenario_id, to_version)
        if not old_items and not new_items:
            return {"available": False, "changed": False}

        def summarize(items):
            names = [x.baseline_name for x in items]
            jiras = sorted({jira for x in items for jira in (x.jira_ids or [])})
            statuses = [x.status for x in items]
            return {"names": names, "jiras": jiras, "statuses": statuses}

        old_summary = summarize(old_items)
        new_summary = summarize(new_items)
        changed = old_summary != new_summary
        old_latest = old_items[-1] if old_items else None
        new_latest = new_items[-1] if new_items else None

        return {
            "available": True,
            "changed": changed,
            "from_name": ", ".join(old_summary["names"]) if old_summary["names"] else None,
            "to_name": ", ".join(new_summary["names"]) if new_summary["names"] else None,
            "from_test_scenarios": old_summary["names"],
            "to_test_scenarios": new_summary["names"],
            "request_changes": self._json_diff(old_latest.request_json if old_latest else None, new_latest.request_json if new_latest else None),
            "expected_response_changes": self._json_diff(old_latest.expected_response_json if old_latest else None, new_latest.expected_response_json if new_latest else None),
            "actual_response_changes": self._json_diff(old_latest.actual_response_json if old_latest else None, new_latest.actual_response_json if new_latest else None),
            "db_effect_changed": (old_latest.expected_db_effect if old_latest else None) != (new_latest.expected_db_effect if new_latest else None),
            "from_db_effect": old_latest.expected_db_effect if old_latest else None,
            "to_db_effect": new_latest.expected_db_effect if new_latest else None,
            "jira_changed": old_summary["jiras"] != new_summary["jiras"],
            "from_jiras": old_summary["jiras"],
            "to_jiras": new_summary["jiras"],
            "status_changed": old_summary["statuses"] != new_summary["statuses"],
            "from_status": ", ".join(old_summary["statuses"]) if old_summary["statuses"] else None,
            "to_status": ", ".join(new_summary["statuses"]) if new_summary["statuses"] else None,
        }

    def _ensure_relevant_change_before_new_version(
        self,
        db: Session,
        scenario,
        latest: ScenarioBaseline,
        endpoint_flow: dict | None
    ):
        """Block V2/V3 creation when the operation's relevant source/flow did not change."""
        latest_snapshot = self.baseline_repository.find_source_snapshot(db, latest.id)

        class_names = set(self._normalize_list(scenario.involved_classes))
        class_names.update(self._flow_classes(endpoint_flow))
        class_names.update(self._flow_classes(latest.endpoint_flow))

        project_path = Path(settings.JAVA_PROJECT_PATH).resolve()
        current_source = self._read_relevant_java_sources(
            project_path=project_path,
            class_names=class_names
        )

        # If an older baseline predates source snapshots we cannot safely claim
        # the code is unchanged, so allow one capture to establish the new
        # source-aware history. From then on duplicate versions are blocked.
        if latest_snapshot and latest_snapshot.source_snapshot is not None:
            previous_source, _ = unpack(latest_snapshot.source_snapshot)
            if latest_snapshot.source_snapshot.get("format_version") != 2:
                current_source = {path: text.replace("\r\n", "\n").replace("\r", "\n")
                                  for path, text in current_source.items()}
            wanted = {str(name).split('.')[-1] for name in class_names}
            previous_source = {path: content for path, content in previous_source.items()
                               if not wanted or Path(path).stem in wanted}
            source_changed = current_source != previous_source
            flow_changed = self._flow_signature(endpoint_flow) != self._flow_signature(latest.endpoint_flow)

            if not source_changed and not flow_changed:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "No relevant code or flow change detected. A new code baseline version was not created.",
                        "active_version": latest.baseline_version,
                        "scenario_code": scenario.scenario_code,
                        "operation": f"{scenario.http_method} {scenario.endpoint}",
                        "suggestion": "Add a Testing Baseline for a new month/test cycle instead."
                    }
                )

    @staticmethod
    def _flow_signature(flow) -> str:
        if flow is None:
            return ""
        if isinstance(flow, str):
            try:
                flow = json.loads(flow)
            except Exception:
                return flow
        try:
            return json.dumps(flow, sort_keys=True, separators=(",", ":"), default=str)
        except Exception:
            return str(flow)

    def _capture_source_snapshot(
        self,
        db: Session,
        baseline: ScenarioBaseline
    ):
        """
        Persist the source state that belongs to a baseline version.

        Store a complete Java-source inventory so changing flow membership
        cannot masquerade as source-file additions or deletions.
        """
        try:
            project_path = Path(
                settings.JAVA_PROJECT_PATH
            ).resolve()

            snapshot = capture_sources(project_path)

            git_diff = self._current_git_diff(
                project_path
            )

            source_changes = self._classify_git_diff(
                git_diff
            )

            record = ScenarioBaselineSourceSnapshot(
                baseline_id=baseline.id,
                project_path=str(project_path),
                source_snapshot=snapshot,
                git_diff=git_diff or None,
                source_changes=source_changes,
            )

            self.baseline_repository.create_source_snapshot(
                db,
                record
            )

        except Exception:
            # A baseline capture must not fail just because source snapshotting
            # is unavailable (for example, a non-Git copy of a Java project).
            db.rollback()


    def _flow_classes(
        self,
        flow
    ) -> set[str]:
        classes = set()

        if not flow:
            return classes

        if isinstance(flow, str):
            try:
                flow = json.loads(flow)
            except Exception:
                return classes

        def walk(value):
            if isinstance(value, dict):
                class_name = value.get(
                    "class_name"
                )
                if class_name:
                    classes.add(
                        class_name
                    )

                for child in value.values():
                    walk(child)

            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(flow)

        return classes


    def _read_relevant_java_sources(
        self,
        project_path: Path,
        class_names: set[str]
    ) -> dict:
        result = {}

        if not project_path.exists():
            return result

        wanted = {
            str(name).split(".")[-1]
            for name in class_names
            if name
        }

        for java_file in project_path.rglob("*.java"):
            if any(part in EXCLUDED for part in java_file.relative_to(project_path).parts):
                continue
            if wanted and java_file.stem not in wanted:
                continue

            try:
                relative = java_file.relative_to(project_path).as_posix()
                with java_file.open(encoding="utf-8", newline="") as stream:
                    result[relative] = stream.read()
            except Exception:
                continue

        return result


    def _git_root(
        self,
        project_path: Path
    ) -> Path | None:
        try:
            result = subprocess.run(
                [
                    "git",
                    "rev-parse",
                    "--show-toplevel"
                ],
                cwd=project_path,
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                return None

            root = result.stdout.strip()

            return (
                Path(root).resolve()
                if root
                else None
            )
        except Exception:
            return None


    def _current_git_diff(
        self,
        project_path: Path
    ) -> str:
        root = self._git_root(
            project_path
        )

        if not root:
            return ""

        parts = []

        for command in (
            ["git", "diff", "--unified=3"],
            ["git", "diff", "--cached", "--unified=3"],
        ):
            result = subprocess.run(
                command,
                cwd=root,
                capture_output=True,
                text=True
            )

            if (
                result.returncode == 0
                and result.stdout.strip()
            ):
                parts.append(
                    result.stdout.strip()
                )

        return "\n\n".join(parts)


    def _classify_git_diff(
        self,
        raw_diff: str
    ) -> list[dict]:
        """
        Small Java-aware classifier for the Version History UI.
        It intentionally keeps the raw diff as the source of truth.
        """
        if not raw_diff:
            return []

        results = []
        current_file = None
        new_line = None
        old_line = None

        field_pattern = re.compile(
            r"""
            ^\s*
            (?:(?:public|protected|private)\s+)?
            (?:(?:static|final|transient|volatile)\s+)*
            (?P<type>[A-Za-z_][\w<>\[\],.? ]*)
            \s+
            (?P<name>[A-Za-z_]\w*)
            \s*(?:=[^;]+)?;\s*$
            """,
            re.VERBOSE
        )

        method_pattern = re.compile(
            r"""
            ^\s*
            (?:(?:public|protected|private)\s+)?
            (?:(?:static|final|synchronized|abstract)\s+)*
            [A-Za-z_][\w<>\[\],.? ]*
            \s+
            (?P<name>[A-Za-z_]\w*)
            \s*\(
            """,
            re.VERBOSE
        )

        for line in raw_diff.splitlines():
            if line.startswith("--- a/"):
                current_file = line[6:]
                continue
            if line.startswith("+++ b/"):
                current_file = line[6:]
                continue

            if line.startswith("@@"):
                match = re.search(
                    r"-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?",
                    line
                )
                if match:
                    old_line = int(
                        match.group(1)
                    )
                    new_line = int(
                        match.group(2)
                    )
                continue

            if line.startswith("+") and not line.startswith("+++"):
                code = line[1:]

                field = field_pattern.match(
                    code
                )
                method = method_pattern.match(
                    code
                )

                if field:
                    results.append({
                        "change_type": "FIELD_ADDED",
                        "file_path": current_file,
                        "line_number": new_line,
                        "symbol": field.group("name"),
                        "data_type": " ".join(
                            field.group("type").split()
                        ),
                        "code": code.strip()
                    })
                elif method:
                    results.append({
                        "change_type": "METHOD_CHANGED",
                        "file_path": current_file,
                        "line_number": new_line,
                        "symbol": method.group("name"),
                        "code": code.strip()
                    })

                if new_line is not None:
                    new_line += 1
                continue

            if line.startswith("-") and not line.startswith("---"):
                code = line[1:]

                field = field_pattern.match(
                    code
                )

                if field:
                    results.append({
                        "change_type": "FIELD_REMOVED",
                        "file_path": current_file,
                        "line_number": old_line,
                        "symbol": field.group("name"),
                        "data_type": " ".join(
                            field.group("type").split()
                        ),
                        "code": code.strip()
                    })

                if old_line is not None:
                    old_line += 1
                continue

            if old_line is not None:
                old_line += 1
            if new_line is not None:
                new_line += 1

        return results


    @staticmethod
    def _scenario_resource_focus(
        scenario_code: str | None,
        endpoint: str | None,
    ) -> str | None:
        """
        Return a narrow child-resource focus only when the scenario itself is
        explicitly attribute/resource-specific. Broad ADD/UPDATE/DELETE
        scenarios intentionally stay unfiltered.
        """
        code = (scenario_code or "").upper()
        path = (endpoint or "").lower()

        candidates = {
            "CONTACT": ("contact", "phone"),
            "EMAIL": ("email",),
            "ADDRESS": ("address",),
        }

        for focus, tokens in candidates.items():
            if f"_{focus}_" in code or code.endswith(f"_{focus}_UPDATE"):
                return focus.lower()
            if any(f"/{token}" in path for token in tokens):
                return focus.lower()

        return None

    def _filter_source_comparison_for_scenario(
        self,
        scenario_code: str | None,
        endpoint: str | None,
        source_comparison: dict,
    ) -> dict:
        """
        Keep source-level evidence scenario-specific for focused child updates.

        For STUDENT_CONTACT_UPDATE, for example, Student.java remains a broad
        execution dependency but an unrelated field such as `eligibility`
        should not be shown as a contact-scenario change.

        We deliberately apply this only to focused contact/email/address
        scenarios. Full ADD/UPDATE scenarios still show their complete source
        comparison.
        """
        focus = self._scenario_resource_focus(scenario_code, endpoint)
        if not focus or not source_comparison:
            return source_comparison

        result = dict(source_comparison)
        changes = list(result.get("changes") or [])

        aliases = {
            "contact": ("contact", "phone"),
            "email": ("email",),
            "address": ("address",),
        }.get(focus, (focus,))

        def is_relevant_change(change: dict) -> bool:
            file_path = str(change.get("file_path") or "").lower()
            symbol = str(change.get("symbol") or "").lower()
            code = str(change.get("code") or "").lower()

            # A dedicated resource class/file is always directly relevant:
            # ContactDetails.java, ContactDetailsMapper.java, EmailDetails..., etc.
            if any(token in file_path for token in aliases):
                return True

            # In broader files (StudentMapper/EmployeeMapper/etc.), require the
            # changed symbol or classified line itself to refer to the focused
            # resource. This prevents Student.eligibility from leaking into a
            # contact comparison.
            return any(
                token in symbol or token in code
                for token in aliases
            )

        filtered_changes = [
            change for change in changes
            if is_relevant_change(change)
        ]

        result["changes"] = filtered_changes
        result["changed_files"] = self._files_from_git_changes(filtered_changes)

        if changes and not filtered_changes:
            result["message"] = (
                f"Source changes were detected between these versions, but none "
                f"were directly related to this {focus} scenario."
            )
        elif len(filtered_changes) != len(changes):
            result["message"] = (
                f"Showing only source changes directly relevant to this {focus} "
                f"scenario; unrelated edits in broad parent dependencies were hidden."
            )

        return result


    def _compare_source_snapshots(
        self,
        db: Session,
        old_baseline: ScenarioBaseline,
        new_baseline: ScenarioBaseline
    ) -> dict:
        old_snapshot = (
            self.baseline_repository.find_source_snapshot(
                db,
                old_baseline.id
            )
        )
        new_snapshot = (
            self.baseline_repository.find_source_snapshot(
                db,
                new_baseline.id
            )
        )

        old_project_path = getattr(old_snapshot, "project_path", None) if old_snapshot else None
        new_project_path = getattr(new_snapshot, "project_path", None) if new_snapshot else None

        project_mismatch = bool(
            old_project_path
            and new_project_path
            and path_key(old_project_path) != path_key(new_project_path)
        )

        if project_mismatch:
            return {
                "snapshot_status": "PROJECT_MISMATCH",
                "from_project_path": old_project_path,
                "to_project_path": new_project_path,
                "project_mismatch": True,
                "message": "These versions belong to different project paths; source comparison is unavailable.",
                "changed_files": [], "changes": [], "raw_diff": ""
            }

        if (not old_snapshot or not new_snapshot
                or old_snapshot.source_snapshot is None or new_snapshot.source_snapshot is None):
            return {
                "snapshot_status": "UNAVAILABLE",
                "from_project_path": old_project_path,
                "to_project_path": new_project_path,
                "project_mismatch": False,
                "message": (
                    "Source snapshots are unavailable for one or both versions. "
                    "A working-tree Git diff cannot reconstruct this historical comparison."
                ),
                "changed_files": [],
                "changes": [],
                "raw_diff": ""
            }

        comparison = compare_sources(old_snapshot.source_snapshot, new_snapshot.source_snapshot)
        comparison.update({
            "from_project_path": old_project_path,
            "to_project_path": new_project_path,
            "project_mismatch": False,
            "changes": self._classify_git_diff(comparison["raw_diff"]),
            "scope": "Stored Java source files; file changes are not proof of scenario impact",
        })
        return comparison


    @staticmethod
    def _build_snapshot_pair_diff(old_sources: dict, new_sources: dict) -> str:
        chunks = []
        for file_path in sorted(set(old_sources or {}) | set(new_sources or {})):
            before = (old_sources or {}).get(file_path)
            after = (new_sources or {}).get(file_path)
            if before == after:
                continue
            before_lines = (before or "").splitlines()
            after_lines = (after or "").splitlines()
            chunks.extend(difflib.unified_diff(
                before_lines, after_lines,
                fromfile=f"a/{file_path}", tofile=f"b/{file_path}",
                lineterm=""
            ))
        return "\n".join(chunks)

    def _files_from_git_changes(
        self,
        changes: list[dict]
    ) -> list[dict]:
        files = {}

        for change in changes:
            path = change.get(
                "file_path"
            )
            if not path:
                continue

            files[path] = {
                "file_path": path,
                "status": "MODIFIED"
            }

        return list(
            files.values()
        )


    def _flow_methods(self, flow):
        methods = set()
        if not flow:
            return methods
        if isinstance(flow, str):
            try:
                flow = json.loads(flow)
            except Exception:
                return methods

        def walk(value):
            if isinstance(value, dict):
                cls = value.get("class_name")
                method = value.get("method_name")
                if cls and method:
                    methods.add(f"{cls}.{method}")
                for key in ("simplified_flow", "flow", "calls", "children"):
                    if key in value:
                        walk(value[key])
            elif isinstance(value, list):
                for item in value:
                    walk(item)
            elif isinstance(value, str) and "." in value and " " not in value:
                methods.add(value)

        walk(flow)
        return methods

    def _flatten_json(self, value, prefix=""):
        flat = {}
        if isinstance(value, dict):
            for key, item in value.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                flat.update(self._flatten_json(item, path))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                path = f"{prefix}[{index}]"
                flat.update(self._flatten_json(item, path))
        elif prefix:
            flat[prefix] = value
        return flat

    def _json_diff(self, old_value, new_value):
        old_flat = self._flatten_json(old_value or {})
        new_flat = self._flatten_json(new_value or {})
        old_keys, new_keys = set(old_flat), set(new_flat)
        return {
            "added_attributes": [
                {"path": key, "value": new_flat[key]}
                for key in sorted(new_keys - old_keys)
            ],
            "removed_attributes": [
                {"path": key, "value": old_flat[key]}
                for key in sorted(old_keys - new_keys)
            ],
            "changed_attributes": [
                {"path": key, "from": old_flat[key], "to": new_flat[key]}
                for key in sorted(old_keys & new_keys)
                if old_flat[key] != new_flat[key]
            ]
        }

    def _normalize_json(
        self,
        value: Any
    ):

        if value is None:
            return None

        if isinstance(
            value,
            (dict, list)
        ):
            return value

        if isinstance(value, str):

            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return {
                    "value": value
                }

        return {
            "value": value
        }

    def _normalize_list(
        self,
        value: Any
    ):

        if value is None:
            return []

        if isinstance(value, list):
            return value

        if isinstance(value, str):

            try:
                parsed = json.loads(value)

                if isinstance(parsed, list):
                    return parsed

            except json.JSONDecodeError:
                pass

            return [
                item.strip()
                for item in value.split(",")
                if item.strip()
            ]

        return [str(value)]
