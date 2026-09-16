import json
import re
from pathlib import Path

from config import settings



class ProjectRegistryService:
    def __init__(self):
        self.base_dir = Path(__file__).resolve().parents[2]
        self.registry_file = self.base_dir / ".codeintelligence-projects.json"
        self.env_file = self.base_dir / ".env"
        self.index_root = self.base_dir / "rag_indexes"

    def list_projects(self) -> dict:
        paths = set(self._load_registered_paths())
        if settings.JAVA_PROJECT_PATH:
            paths.add(str(Path(settings.JAVA_PROJECT_PATH)))

        # Smart-RAG indexes also act as project history.
        if self.index_root.exists():
            for manifest in self.index_root.glob("*/manifest.json"):
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                    project_path = data.get("project_path")
                    if project_path:
                        paths.add(project_path)
                except Exception:
                    pass

        projects = []
        active = self._normalized(settings.JAVA_PROJECT_PATH)
        for raw_path in sorted(paths, key=lambda p: Path(p).name.lower()):
            path = Path(raw_path)
            normalized = self._normalized(str(path))
            projects.append({
                "name": path.name or str(path),
                "path": str(path),
                "exists": path.exists(),
                "active": normalized == active,
            })

        return {
            "active_project_path": settings.JAVA_PROJECT_PATH,
            "projects": projects,
        }

    def register(self, project_path: str) -> dict:
        path = Path(project_path.strip())
        if not path.exists() or not path.is_dir():
            raise ValueError(f"Project path does not exist: {project_path}")
        if not list(path.rglob("*.java")):
            raise ValueError(f"No Java files found under: {project_path}")

        paths = set(self._load_registered_paths())
        paths.add(str(path.resolve()))
        self._save_registered_paths(paths)
        return {"name": path.name, "path": str(path.resolve())}

    def select(self, project_path: str) -> str:
        registered = self.register(project_path)
        selected = registered["path"]
        settings.JAVA_PROJECT_PATH = selected
        self._write_env_project_path(selected)
        return selected

    def _load_registered_paths(self) -> list[str]:
        if not self.registry_file.exists():
            return []
        try:
            data = json.loads(self.registry_file.read_text(encoding="utf-8"))
            return [str(item) for item in data.get("projects", []) if item]
        except Exception:
            return []

    def _save_registered_paths(self, paths: set[str]):
        payload = {"projects": sorted(paths)}
        self.registry_file.write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )

    def _write_env_project_path(self, selected: str):
        line = f"JAVA_PROJECT_PATH={selected}"
        if not self.env_file.exists():
            self.env_file.write_text(line + "\n", encoding="utf-8")
            return

        content = self.env_file.read_text(encoding="utf-8")
        if re.search(r"(?m)^JAVA_PROJECT_PATH=.*$", content):
            content = re.sub(
                r"(?m)^JAVA_PROJECT_PATH=.*$",
                lambda _: line,
                content,
            )
        else:
            if content and not content.endswith("\n"):
                content += "\n"
            content += line + "\n"
        self.env_file.write_text(content, encoding="utf-8")

    @staticmethod
    def _normalized(value: str | None) -> str | None:
        if not value:
            return None
        try:
            return str(Path(value).resolve()).lower()
        except Exception:
            return str(value).lower()
