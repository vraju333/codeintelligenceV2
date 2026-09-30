from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path


class FileTextExtractor:
    """Extract plain text from supported knowledge-base upload formats."""

    SUPPORTED_EXTENSIONS = {".txt", ".md", ".json", ".pdf"}

    def extract(self, filename: str, content: bytes) -> str:
        suffix = Path(filename or "").suffix.lower()
        if suffix not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(
                "Unsupported file type. Supported types: "
                + ", ".join(sorted(self.SUPPORTED_EXTENSIONS))
            )
        if not content:
            raise ValueError("Uploaded file is empty")

        if suffix in {".txt", ".md"}:
            return self._decode_text(content)
        if suffix == ".json":
            return self._extract_json(content)
        if suffix == ".pdf":
            return self._extract_pdf(content)
        raise ValueError(f"Unsupported file type: {suffix}")

    @staticmethod
    def _decode_text(content: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-8", "cp1252"):
            try:
                text = content.decode(encoding).strip()
                if text:
                    return text
            except UnicodeDecodeError:
                continue
        raise ValueError("Unable to decode text file")

    def _extract_json(self, content: bytes) -> str:
        raw = self._decode_text(content)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON file: {exc.msg}") from exc
        return json.dumps(value, ensure_ascii=False, indent=2)

    @staticmethod
    def _extract_pdf(content: bytes) -> str:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ValueError("PDF support requires the pypdf package") from exc

        try:
            reader = PdfReader(BytesIO(content))
            pages = []
            for page_number, page in enumerate(reader.pages, start=1):
                text = (page.extract_text() or "").strip()
                if text:
                    pages.append(f"[Page {page_number}]\n{text}")
        except Exception as exc:
            raise ValueError(f"Unable to read PDF: {exc}") from exc

        text = "\n\n".join(pages).strip()
        if not text:
            raise ValueError(
                "No extractable text found in PDF. Scanned/image-only PDFs are not supported yet."
            )
        return text
