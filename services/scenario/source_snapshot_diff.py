"""Immutable Java source snapshots and pairwise, not working-tree, diffs."""
import difflib
import os
import posixpath
from pathlib import Path


EXCLUDED = {".git", ".gradle", ".idea", "build", "target", "node_modules", ".venv"}


def path_key(value):
    return posixpath.normpath(str(value).replace("\\", "/"))


def capture_sources(root):
    root = Path(root)
    files, errors = {}, []
    if not root.is_dir():
        raise ValueError("Java project directory is unavailable")
    for directory, folders, names in os.walk(root, onerror=lambda error: errors.append(str(error))):
        folders[:] = sorted(name for name in folders if name not in EXCLUDED)
        for name in sorted(names):
            if not name.endswith(".java"):
                continue
            file = Path(directory) / name
            relative = file.relative_to(root).as_posix()
            try:
                # Preserve CRLF and final-newline differences like Git does.
                with file.open(encoding="utf-8", newline="") as stream:
                    files[relative] = stream.read()
            except (OSError, UnicodeError) as error:
                errors.append(f"{relative}: {error}")
    return {"format_version": 2, "scope": "project-java", "complete": not errors,
            "files": files, "errors": errors}


def unpack(snapshot):
    if not isinstance(snapshot, dict):
        return {}, False
    modern = snapshot.get("format_version") == 2
    source = snapshot.get("files", {}) if modern else snapshot
    result = {}
    for path, content in source.items():
        if not isinstance(content, str):
            raise ValueError("Invalid stored source content")
        key = path_key(path)
        if key in result and result[key] != content:
            raise ValueError("Conflicting historical paths after normalization")
        result[key] = content
    return result, modern and snapshot.get("complete") is True


def unified_patch(path, before, after):
    if before == after:
        return ""
    lines = list(difflib.unified_diff(
        (before or "").splitlines(keepends=True),
        (after or "").splitlines(keepends=True),
        fromfile=f"a/{path}" if before is not None else "/dev/null",
        tofile=f"b/{path}" if after is not None else "/dev/null",
    ))
    patch = f"diff --git a/{path} b/{path}\n"
    if before is None:
        patch += "new file mode 100644\n"
    elif after is None:
        patch += "deleted file mode 100644\n"
    for line in lines:
        patch += line
        if not line.endswith("\n"):
            patch += "\n\\ No newline at end of file\n"
    return patch


def compare_sources(old, new):
    before, old_complete = unpack(old)
    after, new_complete = unpack(new)
    if any(not isinstance(item, dict) or item.get("format_version") != 2 for item in (old, new)):
        # Old read_text() captures discarded original line endings. Do not
        # report every Windows file as modified after upgrading the format.
        before = {path: text.replace("\r\n", "\n").replace("\r", "\n") for path, text in before.items()}
        after = {path: text.replace("\r\n", "\n").replace("\r", "\n") for path, text in after.items()}
    complete = old_complete and new_complete
    # Legacy snapshots covered changing flow subsets, not a full project inventory.
    # A missing key in them is not evidence that a file was added or deleted.
    paths = (before.keys() | after.keys()) if complete else (before.keys() & after.keys())
    changed = []
    for path in sorted(paths):
        left, right = before.get(path), after.get(path)
        if left == right:
            continue
        status = "ADDED" if left is None else "REMOVED" if right is None else "MODIFIED"
        patch = unified_patch(path, left, right)
        changed.append({"file_path": path, "status": status, "diff": patch})
    unknown = [] if complete else sorted(before.keys() ^ after.keys())
    return {
        "snapshot_status": "AVAILABLE" if complete else "PARTIAL",
        "changed_files": changed,
        "unverified_files": unknown,
        "raw_diff": "\n".join(item["diff"] for item in changed),
        "message": None if complete else (
            "Historical snapshot coverage is incomplete. Only files stored in both versions "
            "are compared; one-sided files are unverified, not added/deleted. "
            "Two newly captured versions provide full Java source coverage."
        ),
    }
