import hashlib
import json
import re
from pathlib import Path

from langchain_core.documents import Document
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import Language, RecursiveCharacterTextSplitter

from config import settings


class RagService:

    def __init__(self):
        self.embeddings = HuggingFaceEmbeddings(
            model_name="sentence-transformers/all-MiniLM-L6-v2"
        )

        self.splitter = RecursiveCharacterTextSplitter.from_language(
            language=Language.JAVA,
            chunk_size=1200,
            chunk_overlap=150
        )

        # Keep one RAG index per Java project.  This prevents a project switch
        # from accidentally reusing another project's FAISS files.
        self.index_root = Path("rag_indexes")
        self.index_root.mkdir(parents=True, exist_ok=True)

        self.vector_store: FAISS | None = None
        self.current_project_key: str | None = None
        self.current_index_path: Path | None = None

        self._load_current_project_index_if_valid()

    def index_project(self, force: bool = False) -> dict:
        root = self._project_root()
        java_files = sorted(root.rglob("*.java"))

        project_key = self._project_key(root)
        index_path = self.index_root / project_key
        manifest_path = index_path / "manifest.json"
        fingerprint = self._project_fingerprint(root, java_files)

        existing_manifest = self._read_manifest(manifest_path)
        index_exists = self._index_files_exist(index_path)

        # If exactly the same source is already indexed, simply load/reuse it.
        if (
            not force
            and index_exists
            and existing_manifest
            and existing_manifest.get("project_path") == str(root.resolve())
            and existing_manifest.get("fingerprint") == fingerprint
        ):
            self._load_index(index_path, project_key)
            return self._result_from_manifest(
                existing_manifest,
                status="reused",
                message="Project source is unchanged; existing project-specific RAG index reused."
            )

        # If an index exists for this project but the source fingerprint changed,
        # rebuild it in place.  Otherwise this is the first index for the project.
        status = "updated" if index_exists or existing_manifest else "created"

        documents: list[Document] = []

        for java_file in java_files:
            content = java_file.read_text(
                encoding="utf-8",
                errors="ignore"
            )

            class_name = self._extract_class_name(content)
            package_name = self._extract_package(content)
            methods = self._extract_methods(content)

            common_metadata = {
                "file_name": java_file.name,
                "file_path": str(java_file.resolve()),
                "relative_path": str(java_file.relative_to(root)),
                "package_name": package_name,
                "class_name": class_name,
                "language": "java",
                "project_key": project_key,
                "project_path": str(root.resolve())
            }

            if methods:
                for method_name, method_content in methods:
                    documents.append(
                        Document(
                            page_content=method_content,
                            metadata={
                                **common_metadata,
                                "method_name": method_name,
                                "chunk_type": "method"
                            }
                        )
                    )
            else:
                documents.append(
                    Document(
                        page_content=content,
                        metadata={
                            **common_metadata,
                            "method_name": None,
                            "chunk_type": "class"
                        }
                    )
                )

        if not documents:
            self.vector_store = None
            raise RuntimeError(
                f"No Java source documents were found under: {root}"
            )

        chunks = self.splitter.split_documents(documents)

        self.vector_store = FAISS.from_documents(
            documents=chunks,
            embedding=self.embeddings
        )

        index_path.mkdir(parents=True, exist_ok=True)
        self.vector_store.save_local(str(index_path))

        manifest = {
            "project_key": project_key,
            "project_name": root.name,
            "project_path": str(root.resolve()),
            "fingerprint": fingerprint,
            "java_files": len(java_files),
            "documents": len(documents),
            "chunks": len(chunks)
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8"
        )

        self.current_project_key = project_key
        self.current_index_path = index_path

        return {
            **manifest,
            "index_path": str(index_path),
            "status": status,
            "message": (
                "Existing index updated because Java source changed."
                if status == "updated"
                else "New project-specific RAG index created."
            )
        }

    def search(
        self,
        query: str,
        top_k: int = 5
    ) -> list[dict]:
        # Protect against a stale in-memory vector store if JAVA_PROJECT_PATH
        # points at a different project on a newly started process/configuration.
        expected_key = self._project_key(self._project_root())
        if self.current_project_key != expected_key:
            self.index_project()

        if self.vector_store is None:
            raise RuntimeError(
                "RAG index does not exist. Call /api/rag/index first."
            )

        matches = self.vector_store.similarity_search_with_score(
            query=query,
            k=top_k
        )

        results = []

        for document, score in matches:
            results.append(
                {
                    "file_name": document.metadata.get("file_name"),
                    "class_name": document.metadata.get("class_name"),
                    "method_name": document.metadata.get("method_name"),
                    "chunk_type": document.metadata.get("chunk_type"),
                    "file_path": document.metadata.get("file_path"),
                    "project_key": document.metadata.get("project_key"),
                    "similarity_score": float(score),
                    "content": document.page_content
                }
            )

        return results

    def _load_current_project_index_if_valid(self):
        try:
            root = self._project_root()
        except RuntimeError:
            self.vector_store = None
            return

        java_files = sorted(root.rglob("*.java"))
        project_key = self._project_key(root)
        index_path = self.index_root / project_key
        manifest = self._read_manifest(index_path / "manifest.json")

        if not manifest or not self._index_files_exist(index_path):
            self.vector_store = None
            return

        fingerprint = self._project_fingerprint(root, java_files)
        if (
            manifest.get("project_path") != str(root.resolve())
            or manifest.get("fingerprint") != fingerprint
        ):
            self.vector_store = None
            return

        try:
            self._load_index(index_path, project_key)
        except Exception:
            self.vector_store = None
            self.current_project_key = None
            self.current_index_path = None

    def _load_index(self, index_path: Path, project_key: str):
        self.vector_store = FAISS.load_local(
            str(index_path),
            self.embeddings,
            allow_dangerous_deserialization=True
        )
        self.current_project_key = project_key
        self.current_index_path = index_path

    def _project_root(self) -> Path:
        project_path = settings.JAVA_PROJECT_PATH
        if not project_path:
            raise RuntimeError("JAVA_PROJECT_PATH is not configured")

        root = Path(project_path).expanduser()
        if not root.exists():
            raise RuntimeError(
                f"Project path does not exist: {project_path}"
            )
        if not root.is_dir():
            raise RuntimeError(
                f"JAVA_PROJECT_PATH must point to a directory: {project_path}"
            )
        return root.resolve()

    def _project_key(self, root: Path) -> str:
        # Folder name keeps it readable; path hash avoids collisions between
        # projects with the same directory name in different locations.
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", root.name).strip("-") or "java-project"
        path_hash = hashlib.sha1(
            str(root.resolve()).lower().encode("utf-8")
        ).hexdigest()[:10]
        return f"{safe_name}-{path_hash}"

    def _project_fingerprint(self, root: Path, java_files: list[Path]) -> str:
        digest = hashlib.sha256()
        for java_file in java_files:
            relative = str(java_file.relative_to(root)).replace("\\", "/")
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            try:
                digest.update(java_file.read_bytes())
            except OSError:
                # The scanner would also fail to meaningfully analyze a file that
                # cannot be read; including a marker ensures the fingerprint is
                # deterministic instead of silently matching stale content.
                digest.update(b"<unreadable>")
            digest.update(b"\0")
        return digest.hexdigest()

    def _index_files_exist(self, index_path: Path) -> bool:
        return (
            (index_path / "index.faiss").is_file()
            and (index_path / "index.pkl").is_file()
        )

    def _read_manifest(self, manifest_path: Path) -> dict | None:
        if not manifest_path.is_file():
            return None
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _result_from_manifest(self, manifest: dict, status: str, message: str) -> dict:
        project_key = manifest.get("project_key")
        index_path = self.index_root / project_key
        return {
            **manifest,
            "index_path": str(index_path),
            "status": status,
            "message": message
        }

    def _extract_package(self, content: str) -> str | None:
        match = re.search(r"package\s+([\w.]+)\s*;", content)
        return match.group(1) if match else None

    def _extract_class_name(self, content: str) -> str | None:
        match = re.search(r"\b(class|interface|enum|record)\s+(\w+)", content)
        return match.group(2) if match else None

    def _extract_methods(self, content: str) -> list[tuple[str, str]]:
        method_pattern = re.compile(
            r"""
            (?:public|protected|private)
            \s+
            (?:static\s+)?
            (?:final\s+)?
            (?:synchronized\s+)?
            (?:<[^>]+>\s+)?
            [\w<>\[\],.?]+\s+
            (?P<method_name>\w+)
            \s*
            \([^)]*\)
            \s*
            (?:throws\s+[^{]+)?
            \{
            """,
            re.VERBOSE | re.MULTILINE
        )

        methods = []
        for match in method_pattern.finditer(content):
            method_name = match.group("method_name")
            opening_brace = content.find("{", match.start())
            closing_brace = self._find_matching_brace(content, opening_brace)
            if closing_brace == -1:
                continue
            method_content = content[match.start():closing_brace + 1]
            methods.append((method_name, method_content.strip()))

        return methods

    def _find_matching_brace(self, content: str, opening_brace: int) -> int:
        depth = 0
        in_string = False
        escape = False

        for index in range(opening_brace, len(content)):
            character = content[index]

            if character == "\\" and not escape:
                escape = True
                continue

            if character == '"' and not escape:
                in_string = not in_string

            escape = False

            if in_string:
                continue

            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    return index

        return -1
