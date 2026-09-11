import json
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

        latest = (
            self.baseline_repository.find_latest(
                db,
                scenario_id
            )
        )

        if latest:
            self._ensure_relevant_change_before_new_version(
                db=db,
                scenario=scenario,
                latest=latest,
                endpoint_flow=endpoint_flow
            )

        next_version = (
            latest.baseline_version + 1
            if latest
            else 1
        )

        self.baseline_repository.deactivate_existing(
            db,
            scenario_id
        )

        baseline = ScenarioBaseline(
            scenario_id=scenario.id,
            scenario_code=scenario.scenario_code,
            baseline_version=next_version,
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

    def create_test_baseline(
        self,
        db: Session,
        scenario_id: int,
        baseline_name: str,
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

        existing = self.baseline_repository.find_test_baseline_by_name(
            db, scenario_id, name
        )
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Testing baseline '{name}' already exists for this operation"
            )

        active_code_baseline = self.baseline_repository.find_active(db, scenario_id)
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
            code_baseline_version=(
                active_code_baseline.baseline_version if active_code_baseline else None
            )
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

        # A scenario baseline is meant to explain changes relevant to that
        # scenario, not every edit in a broad parent entity that happens to
        # appear in its stored execution flow. Example: adding Student.eligibility
        # must not make STUDENT_CONTACT_UPDATE look changed.
        source_comparison = self._filter_source_comparison_for_scenario(
            scenario_code=scenario.scenario_code,
            endpoint=new.endpoint,
            source_comparison=source_comparison,
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

        return {
            "scenario_id": scenario_id,
            "scenario_code": scenario.scenario_code,
            "from_version": from_version,
            "to_version": to_version,
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
                "version_transition": f"V{from_version} → V{to_version}",
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
                ),
            },
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
            previous_source = latest_snapshot.source_snapshot or {}
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

        We snapshot only classes participating in the scenario flow, rather
        than the entire Java project.  We also store the current Git diff so
        the first capture after this feature can still explain V1 -> V2 even
        when V1 predates source snapshots.
        """
        try:
            project_path = Path(
                settings.JAVA_PROJECT_PATH
            ).resolve()

            class_names = set(
                baseline.involved_classes or []
            )
            class_names.update(
                self._flow_classes(
                    baseline.endpoint_flow
                )
            )

            snapshot = self._read_relevant_java_sources(
                project_path=project_path,
                class_names=class_names
            )

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
            if wanted and java_file.stem not in wanted:
                continue

            try:
                relative = str(
                    java_file.relative_to(
                        project_path
                    )
                ).replace("\\\\", "/")

                result[relative] = (
                    java_file.read_text(
                        encoding="utf-8"
                    )
                )
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
            and Path(old_project_path).resolve() != Path(new_project_path).resolve()
        )

        # If the older baseline predates this feature, the Git diff captured
        # alongside the newer baseline is the best available V1 -> V2 evidence.
        if not old_snapshot and new_snapshot:
            return {
                "snapshot_status": "PREVIOUS_SNAPSHOT_UNAVAILABLE",
                "from_project_path": old_project_path,
                "to_project_path": new_project_path,
                "project_mismatch": False,
                "message": (
                    f"V{old_baseline.baseline_version} was captured before "
                    "source snapshots were enabled. Showing the Git changes "
                    f"stored when V{new_baseline.baseline_version} was captured."
                ),
                "changed_files": self._files_from_git_changes(
                    new_snapshot.source_changes or []
                ),
                "changes": new_snapshot.source_changes or [],
                "raw_diff": new_snapshot.git_diff or ""
            }

        if not old_snapshot or not new_snapshot:
            return {
                "snapshot_status": "UNAVAILABLE",
                "from_project_path": old_project_path,
                "to_project_path": new_project_path,
                "project_mismatch": False,
                "message": (
                    "Source snapshots are unavailable for one or both versions."
                ),
                "changed_files": [],
                "changes": [],
                "raw_diff": ""
            }

        old_sources = old_snapshot.source_snapshot or {}
        new_sources = new_snapshot.source_snapshot or {}

        old_files = set(
            old_sources
        )
        new_files = set(
            new_sources
        )

        changed_files = []

        for file_path in sorted(
            old_files | new_files
        ):
            before = old_sources.get(
                file_path
            )
            after = new_sources.get(
                file_path
            )

            if before == after:
                continue

            if before is None:
                status = "ADDED"
            elif after is None:
                status = "REMOVED"
            else:
                status = "MODIFIED"

            changed_files.append({
                "file_path": file_path,
                "status": status
            })

        # Prefer the Git classification captured with V2 because it identifies
        # Java fields/methods cleanly.  Snapshot file comparison remains the
        # durable evidence even after Git moves on.
        changes = (
            new_snapshot.source_changes
            or []
        )

        return {
            "snapshot_status": "AVAILABLE",
            "from_project_path": old_project_path,
            "to_project_path": new_project_path,
            "project_mismatch": project_mismatch,
            "message": (
                "These two baselines were captured from different Java project paths. "
                "Execution-flow differences are shown, but they must not be interpreted "
                "as source-file additions or deletions across one project."
                if project_mismatch
                else None
            ),
            "changed_files": ([] if project_mismatch else changed_files),
            "changes": ([] if project_mismatch else changes),
            "raw_diff": ("" if project_mismatch else (new_snapshot.git_diff or ""))
        }


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