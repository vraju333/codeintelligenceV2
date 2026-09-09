from pathlib import Path
import re

from fastapi import HTTPException

from config import settings
from schemas import JavaClassInfo, JavaMethod, ScanResponse


class JavaScannerService:

    CLASS_PATTERN = re.compile(
        r'\b(class|interface|enum|record)\s+([A-Za-z_][A-Za-z0-9_]*)'
        r'(?:\s+extends\s+([A-Za-z_][A-Za-z0-9_<>., ?]*))?'
        r'(?:\s+implements\s+([A-Za-z_][A-Za-z0-9_<>,. ?]*))?'
    )

    METHOD_PATTERN = re.compile(
        r'(?:public|protected|private)\s+'
        r'(?:static\s+)?'
        r'(?:final\s+)?'
        r'([A-Za-z_][A-Za-z0-9_<>\[\],.? ]*)\s+'
        r'([A-Za-z_][A-Za-z0-9_]*)\s*'
        r'\(([^)]*)\)\s*'
        r'(?:throws\s+[^{]+)?\{'
    )

    def scan(self) -> ScanResponse:
        project_path = settings.JAVA_PROJECT_PATH

        if not project_path:
            raise HTTPException(
                status_code=500,
                detail="JAVA_PROJECT_PATH is not configured in .env"
            )

        root = Path(project_path)

        if not root.exists():
            raise HTTPException(
                status_code=400,
                detail=f"Java project path does not exist: {project_path}"
            )

        java_files = list(root.rglob("*.java"))
        classes = []

        for java_file in java_files:
            info = self._scan_file(java_file)
            if info:
                classes.append(info)

        return ScanResponse(
            project_path=str(root.resolve()),
            total_java_files=len(java_files),
            classes=classes
        )

    def _scan_file(self, java_file: Path):
        try:
            source = java_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return None

        package_match = re.search(r'package\s+([A-Za-z0-9_.]+)\s*;', source)
        package_name = package_match.group(1) if package_match else None

        imports = re.findall(r'import\s+([A-Za-z0-9_.*]+)\s*;', source)
        annotations = re.findall(
            r'(?m)^\s*@([A-Za-z_][A-Za-z0-9_.]*(?:\([^\n]*\))?)',
            source
        )

        class_match = self.CLASS_PATTERN.search(source)
        if not class_match:
            return None

        class_type = class_match.group(1)
        class_name = class_match.group(2)
        extends_name = class_match.group(3).strip() if class_match.group(3) else None

        implements = []
        if class_match.group(4):
            implements = [item.strip() for item in class_match.group(4).split(",")]

        methods = []

        for method_match in self.METHOD_PATTERN.finditer(source):
            return_type = " ".join(method_match.group(1).split())
            method_name = method_match.group(2)
            parameters_text = method_match.group(3).strip()

            parameters = []
            if parameters_text:
                parameters = [parameter.strip() for parameter in parameters_text.split(",")]

            if method_name != class_name:
                methods.append(
                    JavaMethod(
                        name=method_name,
                        return_type=return_type,
                        parameters=parameters
                    )
                )

        return JavaClassInfo(
            file_path=str(java_file.resolve()),
            package=package_name,
            class_name=class_name,
            class_type=class_type,
            extends=extends_name,
            implements=implements,
            annotations=annotations,
            imports=imports,
            methods=methods
        )
