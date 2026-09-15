import ast
import tempfile
import unittest
import re
from pathlib import Path
from types import SimpleNamespace

from services.scenario.source_snapshot_diff import capture_sources, compare_sources, path_key, unpack
from services.scenario.baseline_risk_report import build_risk_report


def snapshot(files):
    return {"format_version": 2, "complete": True, "scope": "project-java", "files": files}


class BaselineDiffTests(unittest.TestCase):
    def test_adjacent_not_cumulative(self):
        v1 = snapshot({"A.java": "class A {}\n", "B.java": "class B {}\n"})
        v2 = snapshot({"A.java": "class A { int age; }\n", "B.java": "class B {}\n"})
        v3 = snapshot({"A.java": "class A { int age; }\n", "B.java": "class B { int id; }\n"})
        self.assertEqual([x["file_path"] for x in compare_sources(v2, v3)["changed_files"]], ["B.java"])
        self.assertEqual(len(compare_sources(v1, v3)["changed_files"]), 2)

    def test_path_separator_only_is_not_change(self):
        self.assertEqual(compare_sources(snapshot({"src\\A.java": "a"}), snapshot({"src/A.java": "a"}))["changed_files"], [])

    def test_real_add_delete_reverse_and_empty(self):
        a, b = snapshot({"old.java": ""}), snapshot({"new.java": ""})
        result = {x["file_path"]: x["status"] for x in compare_sources(a, b)["changed_files"]}
        self.assertEqual(result, {"old.java": "REMOVED", "new.java": "ADDED"})
        result = {x["file_path"]: x["status"] for x in compare_sources(b, a)["changed_files"]}
        self.assertEqual(result["old.java"], "ADDED")

    def test_legacy_membership_not_addition(self):
        result = compare_sources({"A.java": "a"}, {"A.java": "b", "B.java": "c"})
        self.assertEqual(result["snapshot_status"], "PARTIAL")
        self.assertEqual(result["unverified_files"], ["B.java"])
        self.assertEqual(len(result["changed_files"]), 1)

    def test_final_newline(self):
        diff = compare_sources(snapshot({"A.java": "a"}), snapshot({"A.java": "a\n"}))["raw_diff"]
        self.assertIn("No newline at end of file", diff)
        self.assertIn("-a", diff)
        self.assertIn("+a", diff)

    def test_incomplete_read_is_not_deletion(self):
        new = snapshot({})
        new["complete"] = False
        result = compare_sources(snapshot({"A.java": "a"}), new)
        self.assertEqual(result["changed_files"], [])
        self.assertEqual(result["unverified_files"], ["A.java"])

    def test_capture_exclusions_and_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "A.java").write_bytes(b"class A {}\r\n")
            (root / "target").mkdir()
            (root / "target/Generated.java").write_text("generated")
            result = capture_sources(root)
            self.assertTrue(result["complete"])
            self.assertEqual(result["files"], {"A.java": "class A {}\r\n"})

    def test_legacy_to_modern_not_all_added(self):
        result = compare_sources({"A.java": "a"}, snapshot({"A.java": "a", "B.java": "b"}))
        self.assertEqual(result["changed_files"], [])
        self.assertEqual(result["snapshot_status"], "PARTIAL")

    def test_legacy_windows_line_endings_not_all_modified(self):
        result = compare_sources({"A.java": "a\n"}, snapshot({"A.java": "a\r\n"}))
        self.assertEqual(result["changed_files"], [])

    def test_new_format_preserves_line_ending_changes(self):
        result = compare_sources(snapshot({"A.java": "a\n"}), snapshot({"A.java": "a\r\n"}))
        self.assertEqual(len(result["changed_files"]), 1)


class ServiceWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Execute the actual comparison/classifier methods without requiring the
        # full application stack, database credentials, or embedding model.
        source = Path(__file__).resolve().parents[1] / "services/scenario/scenario_baseline_service.py"
        tree = ast.parse(source.read_text())
        service = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        service.body = [node for node in service.body if isinstance(node, ast.FunctionDef)
                        and node.name in {"_compare_source_snapshots", "_classify_git_diff"}]
        namespace = {"Session": object, "ScenarioBaseline": object, "re": re,
                     "path_key": path_key, "compare_sources": compare_sources}
        exec(compile(ast.Module(body=[service], type_ignores=[]), str(source), "exec"), namespace)
        cls.service_type = namespace["ScenarioBaselineService"]

    def compare(self, a, b):
        service = self.service_type()
        service.baseline_repository = SimpleNamespace(find_source_snapshot=lambda db, id: {1: a, 2: b}[id])
        return service._compare_source_snapshots(None, SimpleNamespace(id=1), SimpleNamespace(id=2))

    def row(self, data, project="D:/project"):
        return SimpleNamespace(project_path=project, source_snapshot=data, git_diff="unrelated", source_changes=["wrong"])

    def test_missing_snapshot_does_not_use_git(self):
        result = self.compare(None, self.row(snapshot({"A.java": "a"})))
        self.assertEqual(result["raw_diff"], "")
        self.assertEqual(result["snapshot_status"], "UNAVAILABLE")

    def test_project_mismatch(self):
        result = self.compare(self.row(snapshot({}), "D:/one"), self.row(snapshot({}), "D:/two"))
        self.assertEqual(result["snapshot_status"], "PROJECT_MISMATCH")

    def test_deleted_java_field_classification(self):
        result = self.compare(self.row(snapshot({"A.java": "private int age;\n"})), self.row(snapshot({})))
        self.assertTrue(result["changes"])
        self.assertEqual(result["changes"][0]["file_path"], "A.java")


class RiskTests(unittest.TestCase):
    def test_unknown_not_low(self):
        result = build_risk_report({"snapshot_status": "PARTIAL"}, False, False, [], [], [], "ADD", ["A"])
        self.assertEqual(result["risk_level"], "UNKNOWN")
        self.assertFalse(result["llm_used"])

    def test_failed_test_and_candidate(self):
        source = {"snapshot_status": "AVAILABLE", "changed_files": [{"file_path": "src/A.java"}]}
        test = SimpleNamespace(status="FAIL", baseline_name="bad input", jira_ids=["J-1"])
        result = build_risk_report(source, False, False, [], [], [test], "ADD", ["com.A"])
        self.assertEqual(result["risk_level"], "HIGH")
        self.assertEqual(result["score"], 60)

    def test_unrelated_file_not_candidate(self):
        source = {"snapshot_status": "AVAILABLE", "changed_files": [{"file_path": "src/Other.java"}]}
        result = build_risk_report(source, False, False, [], [], [], "ADD", ["A"])
        self.assertEqual(result["candidate_files"], [])


if __name__ == "__main__":
    unittest.main()
