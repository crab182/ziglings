import logging
from io import BytesIO
from pathlib import Path

import chardet

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml",
    ".xml", ".html", ".css", ".csv", ".log", ".cfg", ".ini", ".conf",
    ".sh", ".bash", ".zsh", ".bat", ".ps1", ".sql", ".r", ".go",
    ".java", ".c", ".cpp", ".h", ".hpp", ".rs", ".toml", ".zig",
    ".pdf", ".docx", ".xlsx",
}


def can_parse(filename: str) -> bool:
    return Path(filename).suffix.lower() in SUPPORTED_EXTENSIONS


def parse_file(file_path: str | None = None, content: bytes | None = None, filename: str = "") -> str:
    if file_path:
        filename = filename or file_path
        with open(file_path, "rb") as f:
            content = f.read()

    if content is None:
        return ""

    ext = Path(filename).suffix.lower()

    try:
        if ext == ".pdf":
            return _parse_pdf(content)
        elif ext == ".docx":
            return _parse_docx(content)
        elif ext == ".xlsx":
            return _parse_xlsx(content)
        else:
            return _parse_text(content)
    except Exception as e:
        logger.error(f"Failed to parse {filename}: {e}")
        return ""


def _parse_text(content: bytes) -> str:
    detected = chardet.detect(content)
    encoding = detected.get("encoding", "utf-8") or "utf-8"
    try:
        return content.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        return content.decode("utf-8", errors="replace")


def _parse_pdf(content: bytes) -> str:
    from PyPDF2 import PdfReader
    reader = PdfReader(BytesIO(content))
    text_parts = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            text_parts.append(text)
    return "\n\n".join(text_parts)


def _parse_docx(content: bytes) -> str:
    from docx import Document
    doc = Document(BytesIO(content))
    return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _parse_xlsx(content: bytes) -> str:
    from openpyxl import load_workbook
    wb = load_workbook(BytesIO(content), read_only=True)
    text_parts = []
    for sheet in wb.sheetnames:
        ws = wb[sheet]
        text_parts.append(f"--- Sheet: {sheet} ---")
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) if c is not None else "" for c in row]
            text_parts.append("\t".join(cells))
    return "\n".join(text_parts)
