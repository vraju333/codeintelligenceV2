import re
import subprocess
from pathlib import Path

from config import settings


class GitDiffService:

    def __init__(self):
        self.project_path = None
        self.git_root = None

    def _refresh_project_path(self):
        if not settings.JAVA_PROJECT_PATH:
            raise RuntimeError(
                "JAVA_PROJECT_PATH is not configured"
            )

        self.project_path = Path(
            settings.JAVA_PROJECT_PATH
        ).resolve()

        if not self.project_path.exists():
            raise RuntimeError(
                f"JAVA_PROJECT_PATH does not exist: {self.project_path}"
            )

    def analyse_changes(self):

        self._refresh_project_path()
        self._validate_git_repository()

        changed_files = self._get_changed_java_files()

        results = []

        for relative_path in changed_files:

            file_path = (
                self.git_root /
                relative_path
            )

            changed_lines = (
                self._get_changed_line_numbers(
                    relative_path
                )
            )

            methods = []

            if file_path.exists():

                methods = (
                    self._find_changed_methods(
                        file_path,
                        changed_lines
                    )
                )

            source_changes = self._analyse_source_changes(
                relative_path=relative_path,
                file_path=file_path,
                changed_methods=methods
            )

            results.append(
                {
                    "file_path": relative_path,
                    "file_name": Path(
                        relative_path
                    ).name,
                    "class_name": Path(
                        relative_path
                    ).stem,
                    "changed_lines": sorted(
                        changed_lines
                    ),
                    "changed_methods": methods,
                    "source_changes": source_changes["changes"],
                    "raw_diff": source_changes["raw_diff"]
                }
            )

        return {
            "project_path": str(
                self.project_path
            ),
            "git_root": str(
                self.git_root
            ),
            "total_changed_java_files": len(
                results
            ),
            "changed_files": results
        }

    def _validate_git_repository(self):

        result = subprocess.run(
            [
                "git",
                "rev-parse",
                "--show-toplevel"
            ],
            cwd=self.project_path,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            raise RuntimeError(
                "Selected Java project is not inside a Git working tree"
            )

        root = result.stdout.strip()

        if not root:
            raise RuntimeError(
                "Unable to determine Git repository root"
            )

        self.git_root = Path(root).resolve()

    def _get_changed_java_files(self):

        files = set()

        commands = [
            [
                "git",
                "diff",
                "--name-only"
            ],
            [
                "git",
                "diff",
                "--cached",
                "--name-only"
            ]
        ]

        for command in commands:

            result = subprocess.run(
                command,
                cwd=self.git_root,
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                continue

            for line in result.stdout.splitlines():

                line = line.strip()

                if line.endswith(".java"):
                    files.add(line)

        return sorted(files)

    def _get_changed_line_numbers(
        self,
        relative_path: str
    ):

        changed_lines = set()

        commands = [
            [
                "git",
                "diff",
                "--unified=0",
                "--",
                relative_path
            ],
            [
                "git",
                "diff",
                "--cached",
                "--unified=0",
                "--",
                relative_path
            ]
        ]

        for command in commands:

            result = subprocess.run(
                command,
                cwd=self.git_root,
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                continue

            for line in result.stdout.splitlines():

                if not line.startswith("@@"):
                    continue

                match = re.search(
                    r"\+(\d+)(?:,(\d+))?",
                    line
                )

                if not match:
                    continue

                start = int(
                    match.group(1)
                )

                count = int(
                    match.group(2) or 1
                )

                if count == 0:
                    continue

                for number in range(
                    start,
                    start + count
                ):
                    changed_lines.add(
                        number
                    )

        return changed_lines

    def _get_file_diff(
        self,
        relative_path: str
    ) -> str:

        parts = []

        commands = [
            [
                "git",
                "diff",
                "--unified=3",
                "--",
                relative_path
            ],
            [
                "git",
                "diff",
                "--cached",
                "--unified=3",
                "--",
                relative_path
            ]
        ]

        for command in commands:

            result = subprocess.run(
                command,
                cwd=self.git_root,
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


    def _analyse_source_changes(
        self,
        relative_path: str,
        file_path: Path,
        changed_methods: list[dict]
    ) -> dict:

        raw_diff = self._get_file_diff(
            relative_path
        )

        if not raw_diff:
            return {
                "changes": [],
                "raw_diff": ""
            }

        changes = []

        added_lines = []
        removed_lines = []

        new_line_number = None
        old_line_number = None

        for line in raw_diff.splitlines():

            if line.startswith("@@"):

                match = re.search(
                    r"-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?",
                    line
                )

                if match:
                    old_line_number = int(
                        match.group(1)
                    )
                    new_line_number = int(
                        match.group(2)
                    )

                continue

            if line.startswith("+++") or line.startswith("---"):
                continue

            if line.startswith("+"):

                added_lines.append(
                    {
                        "line_number": new_line_number,
                        "text": line[1:]
                    }
                )

                if new_line_number is not None:
                    new_line_number += 1

                continue

            if line.startswith("-"):

                removed_lines.append(
                    {
                        "line_number": old_line_number,
                        "text": line[1:]
                    }
                )

                if old_line_number is not None:
                    old_line_number += 1

                continue

            if old_line_number is not None:
                old_line_number += 1

            if new_line_number is not None:
                new_line_number += 1

        field_pattern = re.compile(
            r"""
            ^\s*
            (?:
                public|protected|private
            )
            \s+
            (?:
                static\s+
            )?
            (?:
                final\s+
            )?
            (?P<type>
                [A-Za-z_][\w<>\[\],.? ]*
            )
            \s+
            (?P<name>[A-Za-z_]\w*)
            \s*
            (?:
                =[^;]+
            )?
            ;
            \s*$
            """,
            re.VERBOSE
        )

        added_field_names = set()
        removed_field_names = set()

        for item in added_lines:

            match = field_pattern.match(
                item["text"]
            )

            if not match:
                continue

            field_name = match.group(
                "name"
            )

            added_field_names.add(
                field_name
            )

            changes.append(
                {
                    "change_type": "FIELD_ADDED",
                    "label": "Added field",
                    "symbol": field_name,
                    "data_type": " ".join(
                        match.group("type").split()
                    ),
                    "line_number": item[
                        "line_number"
                    ],
                    "added_text": item[
                        "text"
                    ].strip(),
                    "removed_text": None
                }
            )

        for item in removed_lines:

            match = field_pattern.match(
                item["text"]
            )

            if not match:
                continue

            field_name = match.group(
                "name"
            )

            removed_field_names.add(
                field_name
            )

            # If the same field is also added, it was probably modified
            # (type/default/visibility), not removed.
            if field_name in added_field_names:
                continue

            changes.append(
                {
                    "change_type": "FIELD_REMOVED",
                    "label": "Removed field",
                    "symbol": field_name,
                    "data_type": " ".join(
                        match.group("type").split()
                    ),
                    "line_number": item[
                        "line_number"
                    ],
                    "added_text": None,
                    "removed_text": item[
                        "text"
                    ].strip()
                }
            )

        # Reclassify same-name remove+add pairs as modified fields.
        common_fields = (
            added_field_names
            & removed_field_names
        )

        if common_fields:

            changes = [
                change
                for change in changes
                if not (
                    change.get("symbol")
                    in common_fields
                    and change.get(
                        "change_type"
                    )
                    in {
                        "FIELD_ADDED",
                        "FIELD_REMOVED"
                    }
                )
            ]

            for field_name in sorted(
                common_fields
            ):

                added = next(
                    (
                        item
                        for item in added_lines
                        if (
                            field_pattern.match(
                                item["text"]
                            )
                            and field_pattern.match(
                                item["text"]
                            ).group(
                                "name"
                            )
                            == field_name
                        )
                    ),
                    None
                )

                removed = next(
                    (
                        item
                        for item in removed_lines
                        if (
                            field_pattern.match(
                                item["text"]
                            )
                            and field_pattern.match(
                                item["text"]
                            ).group(
                                "name"
                            )
                            == field_name
                        )
                    ),
                    None
                )

                changes.append(
                    {
                        "change_type": "FIELD_MODIFIED",
                        "label": "Modified field",
                        "symbol": field_name,
                        "data_type": None,
                        "line_number": (
                            added or removed or {}
                        ).get(
                            "line_number"
                        ),
                        "added_text": (
                            added or {}
                        ).get(
                            "text"
                        ),
                        "removed_text": (
                            removed or {}
                        ).get(
                            "text"
                        )
                    }
                )

        for method in changed_methods or []:

            method_name = method.get(
                "method_name"
            )

            if not method_name:
                continue

            method_lines = set(
                method.get(
                    "changed_lines",
                    []
                )
            )

            method_added = [
                item
                for item in added_lines
                if item.get("line_number")
                in method_lines
            ]

            # Removed line numbers are from the old file so exact mapping is
            # not always possible; include nearby removed code in raw diff.
            changes.append(
                {
                    "change_type": "METHOD_MODIFIED",
                    "label": "Modified method",
                    "symbol": method_name,
                    "line_number": (
                        min(method_lines)
                        if method_lines
                        else method.get(
                            "start_line"
                        )
                    ),
                    "changed_lines": sorted(
                        method_lines
                    ),
                    "added_lines": [
                        item["text"].strip()
                        for item in method_added
                        if item["text"].strip()
                    ][:8]
                }
            )

        # If a file changed but no field/method classifier matched, retain a
        # compact generic source-change item rather than hiding the change.
        if not changes:

            changes.append(
                {
                    "change_type": "SOURCE_MODIFIED",
                    "label": "Source changed",
                    "symbol": Path(
                        relative_path
                    ).stem,
                    "line_number": (
                        added_lines[0][
                            "line_number"
                        ]
                        if added_lines
                        else None
                    ),
                    "added_lines": [
                        item["text"].strip()
                        for item in added_lines
                        if item["text"].strip()
                    ][:8],
                    "removed_lines": [
                        item["text"].strip()
                        for item in removed_lines
                        if item["text"].strip()
                    ][:8]
                }
            )

        return {
            "changes": changes,
            "raw_diff": raw_diff
        }


    def _find_changed_methods(
        self,
        file_path: Path,
        changed_lines: set[int]
    ):

        content = file_path.read_text(
            encoding="utf-8"
        )

        lines = content.splitlines()

        method_ranges = (
            self._extract_method_ranges(
                lines
            )
        )

        changed_methods = []

        for method in method_ranges:

            start_line = method[
                "start_line"
            ]

            end_line = method[
                "end_line"
            ]

            affected_lines = [
                line
                for line in changed_lines
                if start_line
                <= line
                <= end_line
            ]

            if not affected_lines:
                continue

            changed_methods.append(
                {
                    "method_name": method[
                        "method_name"
                    ],
                    "start_line": start_line,
                    "end_line": end_line,
                    "changed_lines": sorted(
                        affected_lines
                    )
                }
            )

        return changed_methods

    def _extract_method_ranges(
        self,
        lines: list[str]
    ):

        methods = []

        method_pattern = re.compile(
            r"""
            ^\s*
            (?:
                public|
                protected|
                private
            )
            \s+
            (?:
                static\s+
            )?
            (?:
                final\s+
            )?
            (?:
                synchronized\s+
            )?
            [\w<>\[\],.?]+\s+
            (?P<name>[A-Za-z_]\w*)
            \s*
            \(
            """,
            re.VERBOSE
        )

        index = 0

        while index < len(lines):

            line = lines[index]

            match = method_pattern.search(
                line
            )

            if not match:

                index += 1
                continue

            method_name = match.group(
                "name"
            )

            signature_start = index

            brace_index = index
            found_open_brace = False

            while brace_index < len(lines):

                current = lines[
                    brace_index
                ]

                if "{" in current:
                    found_open_brace = True
                    break

                if ";" in current:
                    break

                brace_index += 1

            if not found_open_brace:

                index += 1
                continue

            brace_count = 0
            method_end = brace_index

            started = False

            for method_end in range(
                brace_index,
                len(lines)
            ):

                current = lines[
                    method_end
                ]

                open_count = current.count(
                    "{"
                )

                close_count = current.count(
                    "}"
                )

                if open_count > 0:
                    started = True

                brace_count += open_count
                brace_count -= close_count

                if started and brace_count == 0:
                    break

            methods.append(
                {
                    "method_name": method_name,
                    "start_line": (
                        signature_start + 1
                    ),
                    "end_line": (
                        method_end + 1
                    )
                }
            )

            index = method_end + 1

        return methods