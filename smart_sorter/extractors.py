from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

# pypdf logs malformed-file warnings straight to stderr; silence them because a
# broken PDF in Downloads is expected and handled by returning empty text.
logging.getLogger("pypdf").setLevel(logging.ERROR)


TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".xml", ".html", ".htm", ".rtf"}
MAX_TEXT_CHARACTERS = 12_000


def _plain_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")[:MAX_TEXT_CHARACTERS]


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        document = archive.read("word/document.xml")
    root = ElementTree.fromstring(document)
    text = " ".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))
    return text[:MAX_TEXT_CHARACTERS]


def _pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]
    except ImportError:
        return ""
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)[:MAX_TEXT_CHARACTERS]


def extract_text(path: Path) -> str:
    """Extract a bounded amount of local text; failures safely return no text.

    Extraction runs over untrusted files pulled from Downloads, so any parser
    error (including third-party pypdf errors) must degrade to empty text rather
    than crash the sorting pipeline.
    """
    try:
        suffix = path.suffix.casefold()
        if suffix in TEXT_EXTENSIONS:
            text = _plain_text(path)
        elif suffix == ".docx":
            text = _docx_text(path)
        elif suffix == ".pdf":
            text = _pdf_text(path)
        else:
            return ""
        return re.sub(r"\s+", " ", text).strip()
    except Exception:
        return ""
