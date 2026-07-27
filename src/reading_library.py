"""Private book library for Garden's shared human/AI reading experience."""

from __future__ import annotations

import hashlib
import io
import json
import os
import posixpath
import re
import tempfile
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import unquote
from xml.etree.ElementTree import Element, ParseError

from defusedxml.ElementTree import fromstring as safe_xml_fromstring
from defusedxml.common import DefusedXmlException


MAX_BOOK_BYTES = 30 * 1024 * 1024
MAX_EPUB_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_EPUB_FILES = 5000
TARGET_CHUNK_CHARS = 4200
MAX_CHUNK_CHARS = 6200
SUPPORTED_EXTENSIONS = {".epub", ".txt", ".md"}


class ReadingLibraryError(ValueError):
    """A user-facing reading-library validation error."""


class _HTMLTextExtractor(HTMLParser):
    _BLOCKS = {
        "address", "article", "aside", "blockquote", "br", "div", "figcaption",
        "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li",
        "main", "nav", "ol", "p", "pre", "section", "table", "tr", "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "svg"}:
            self._ignored += 1
        elif not self._ignored and tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "svg"} and self._ignored:
            self._ignored -= 1
        elif not self._ignored and tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored:
            self.parts.append(data)

    def text(self) -> str:
        return _clean_text("".join(self.parts))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return _clean_text(data.decode(encoding))
        except UnicodeDecodeError:
            continue
    raise ReadingLibraryError("无法识别文本编码，请转换为 UTF-8 后重试")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_text(root: Element, name: str) -> str:
    for item in root.iter():
        if _local_name(item.tag) == name and item.text:
            return item.text.strip()
    return ""


def _safe_zip_member(name: str) -> str:
    normalized = posixpath.normpath(unquote(name).replace("\\", "/")).lstrip("/")
    if normalized == ".." or normalized.startswith("../"):
        raise ReadingLibraryError("EPUB 包含不安全的文件路径")
    return normalized


def _extract_html_text(data: bytes) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(_decode_text(data))
    return parser.text()


def _extract_epub(data: bytes) -> tuple[str, str, list[tuple[str, str]]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ReadingLibraryError("EPUB 文件损坏或格式无效") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_EPUB_FILES or sum(item.file_size for item in infos) > MAX_EPUB_UNCOMPRESSED_BYTES:
            raise ReadingLibraryError("EPUB 解压后的内容过大")
        names = {_safe_zip_member(item.filename): item.filename for item in infos}
        container_name = names.get("META-INF/container.xml")
        if not container_name:
            raise ReadingLibraryError("EPUB 缺少 container.xml")
        try:
            container = safe_xml_fromstring(archive.read(container_name))
            rootfile = next(
                str(item.attrib.get("full-path", ""))
                for item in container.iter()
                if _local_name(item.tag) == "rootfile"
            )
            opf_name = names[_safe_zip_member(rootfile)]
            opf = safe_xml_fromstring(archive.read(opf_name))
        except (ParseError, DefusedXmlException, KeyError, StopIteration) as exc:
            raise ReadingLibraryError("EPUB 目录结构无法解析") from exc

        title = _xml_text(opf, "title")
        author = _xml_text(opf, "creator")
        manifest: dict[str, str] = {}
        spine: list[str] = []
        for item in opf.iter():
            tag = _local_name(item.tag)
            if tag == "item":
                item_id = str(item.attrib.get("id", ""))
                href = str(item.attrib.get("href", ""))
                media_type = str(item.attrib.get("media-type", ""))
                if item_id and href and media_type in {"application/xhtml+xml", "text/html"}:
                    manifest[item_id] = href
            elif tag == "itemref" and item.attrib.get("idref"):
                spine.append(str(item.attrib["idref"]))

        base = posixpath.dirname(_safe_zip_member(rootfile))
        sections: list[tuple[str, str]] = []
        for order, item_id in enumerate(spine, 1):
            href = manifest.get(item_id)
            if not href:
                continue
            member = _safe_zip_member(posixpath.join(base, href))
            original = names.get(member)
            if not original:
                continue
            text = _extract_html_text(archive.read(original))
            if len(text) < 20:
                continue
            heading = next((line.strip("# ") for line in text.splitlines() if line.strip()), "")
            sections.append((heading[:160] or f"Chapter {order}", text))
        if not sections:
            raise ReadingLibraryError("EPUB 中没有可读取的正文")
        return title, author, sections


def _extract_text_sections(text: str, markdown: bool) -> list[tuple[str, str]]:
    if markdown:
        matches = list(re.finditer(r"(?m)^(#{1,3})\s+(.+?)\s*$", text))
        if matches:
            sections: list[tuple[str, str]] = []
            preamble = _clean_text(text[:matches[0].start()])
            if preamble:
                sections.append(("前言", preamble))
            for index, match in enumerate(matches):
                end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
                body = _clean_text(text[match.end():end])
                if body:
                    sections.append((match.group(2).strip()[:160], body))
            if sections:
                return sections
    return [("正文", text)]


def _split_long_paragraph(paragraph: str) -> list[str]:
    sentences = re.split(r"(?<=[。！？!?；;\.])\s*", paragraph)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        if not sentence:
            continue
        if current and len(current) + len(sentence) > MAX_CHUNK_CHARS:
            pieces.append(current)
            current = ""
        while len(sentence) > MAX_CHUNK_CHARS:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:MAX_CHUNK_CHARS])
            sentence = sentence[MAX_CHUNK_CHARS:]
        current += sentence
    if current:
        pieces.append(current)
    return pieces


def _chunk_sections(sections: list[tuple[str, str]]) -> list[dict]:
    chunks: list[dict] = []
    for chapter_index, (heading, text) in enumerate(sections):
        paragraphs: list[str] = []
        for paragraph in re.split(r"\n\s*\n|\n", _clean_text(text)):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            paragraphs.extend(_split_long_paragraph(paragraph))
        groups: list[str] = []
        current: list[str] = []
        current_size = 0
        for paragraph in paragraphs:
            addition = len(paragraph) + (2 if current else 0)
            if current and current_size + addition > TARGET_CHUNK_CHARS:
                groups.append("\n\n".join(current))
                current = []
                current_size = 0
            current.append(paragraph)
            current_size += addition
        if current:
            groups.append("\n\n".join(current))
        for part_index, content in enumerate(groups):
            title = heading if len(groups) == 1 else f"{heading} · {part_index + 1}"
            chunks.append({
                "id": len(chunks),
                "chapter": chapter_index,
                "part": part_index,
                "title": title,
                "text": content,
                "analysis": None,
            })
    if not chunks:
        raise ReadingLibraryError("文件中没有可读取的正文")
    return chunks


class ReadingLibrary:
    """Filesystem-backed private book library with atomic JSON state writes."""

    def __init__(self, buckets_dir: str):
        self.root = os.path.join(str(buckets_dir), ".reading_library")
        os.makedirs(self.root, exist_ok=True)
        self._lock = threading.RLock()

    def _book_dir(self, book_id: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{12}", str(book_id or "")):
            raise ReadingLibraryError("书籍 ID 无效")
        return os.path.join(self.root, book_id)

    def _path(self, book_id: str, name: str) -> str:
        return os.path.join(self._book_dir(book_id), name)

    @staticmethod
    def _read_json(path: str, fallback):
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return fallback

    @staticmethod
    def _write_json(path: str, value) -> None:
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="reading-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def import_book(self, filename: str, data: bytes, *, title: str = "", author: str = "") -> dict:
        if not data or len(data) > MAX_BOOK_BYTES:
            raise ReadingLibraryError("书籍文件必须介于 1 B 与 30 MB 之间")
        extension = os.path.splitext(os.path.basename(filename or ""))[1].lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise ReadingLibraryError("首版只支持 EPUB、TXT 和 Markdown")
        book_id = hashlib.sha256(data).hexdigest()[:12]
        with self._lock:
            existing = self._read_json(self._path(book_id, "book.json"), None)
            if isinstance(existing, dict):
                existing["duplicate"] = True
                return self._public_book(existing)

            if extension == ".epub":
                embedded_title, embedded_author, sections = _extract_epub(data)
            else:
                embedded_title, embedded_author = "", ""
                sections = _extract_text_sections(_decode_text(data), extension == ".md")
            chunks = _chunk_sections(sections)
            os.makedirs(self._book_dir(book_id), exist_ok=True)
            # Never keep a .md suffix inside buckets_dir: GitHub memory backup
            # intentionally scans every Markdown file recursively. The source
            # format remains in book.json, while the private original stays
            # invisible to that sync boundary.
            source_name = "source.book"
            with open(self._path(book_id, source_name), "wb") as handle:
                handle.write(data)
            now = _now()
            book = {
                "id": book_id,
                "title": (title or embedded_title or os.path.splitext(os.path.basename(filename))[0] or "Untitled").strip()[:200],
                "author": (author or embedded_author).strip()[:160],
                "filename": os.path.basename(filename)[:240],
                "format": extension.lstrip("."),
                "chunk_count": len(chunks),
                "created_at": now,
                "updated_at": now,
                "progress": {
                    "human": {"chunk": 0, "offset": 0.0, "updated_at": ""},
                    "ai": {"chunk": 0, "offset": 0.0, "updated_at": ""},
                },
            }
            self._write_json(self._path(book_id, "chunks.json"), chunks)
            self._write_json(self._path(book_id, "annotations.json"), [])
            self._write_json(self._path(book_id, "book.json"), book)
            return self._public_book(book)

    def list_books(self) -> list[dict]:
        rows: list[dict] = []
        try:
            entries = os.listdir(self.root)
        except OSError:
            return rows
        for entry in entries:
            if not re.fullmatch(r"[0-9a-f]{12}", entry):
                continue
            book = self._read_json(os.path.join(self.root, entry, "book.json"), None)
            if isinstance(book, dict):
                rows.append(self._public_book(book))
        return sorted(rows, key=lambda row: row.get("updated_at", ""), reverse=True)

    def get_book(self, book_id: str) -> dict:
        book = self._read_json(self._path(book_id, "book.json"), None)
        if not isinstance(book, dict):
            raise ReadingLibraryError("未找到这本书")
        return self._public_book(book)

    @staticmethod
    def _public_book(book: dict) -> dict:
        result = dict(book)
        result.pop("source_path", None)
        progress = result.get("progress") or {}
        count = max(1, int(result.get("chunk_count") or 1))
        human = progress.get("human") or {}
        result["percent"] = round(
            min(100.0, max(0.0, ((int(human.get("chunk") or 0) + float(human.get("offset") or 0)) / count) * 100)),
            1,
        )
        return result

    def _chunks(self, book_id: str) -> list[dict]:
        chunks = self._read_json(self._path(book_id, "chunks.json"), None)
        if not isinstance(chunks, list) or not chunks:
            raise ReadingLibraryError("书籍正文索引损坏")
        return chunks

    def get_chunk(self, book_id: str, chunk_id: int) -> dict:
        book = self.get_book(book_id)
        chunks = self._chunks(book_id)
        try:
            chunk_id = int(chunk_id)
        except (TypeError, ValueError) as exc:
            raise ReadingLibraryError("阅读位置无效") from exc
        if chunk_id < 0 or chunk_id >= len(chunks):
            raise ReadingLibraryError("阅读位置超出范围")
        chunk = dict(chunks[chunk_id])
        annotations = self._read_json(self._path(book_id, "annotations.json"), [])
        chunk["annotations"] = [item for item in annotations if int(item.get("chunk", -1)) == chunk_id]
        chunk["book"] = book
        chunk["previous"] = chunk_id - 1 if chunk_id > 0 else None
        chunk["next"] = chunk_id + 1 if chunk_id + 1 < len(chunks) else None
        chunk["percent"] = round(((chunk_id + 1) / len(chunks)) * 100, 1)
        return chunk

    def update_progress(self, book_id: str, actor: str, chunk_id: int, offset: float = 0.0) -> dict:
        if actor not in {"human", "ai"}:
            raise ReadingLibraryError("阅读者必须是 human 或 ai")
        chunks = self._chunks(book_id)
        chunk_id = int(chunk_id)
        if chunk_id < 0 or chunk_id >= len(chunks):
            raise ReadingLibraryError("阅读位置超出范围")
        offset = min(1.0, max(0.0, float(offset)))
        with self._lock:
            book = self._read_json(self._path(book_id, "book.json"), None)
            if not isinstance(book, dict):
                raise ReadingLibraryError("未找到这本书")
            now = _now()
            book.setdefault("progress", {})[actor] = {"chunk": chunk_id, "offset": offset, "updated_at": now}
            book["updated_at"] = now
            self._write_json(self._path(book_id, "book.json"), book)
            return self._public_book(book)

    def add_annotation(
        self,
        book_id: str,
        chunk_id: int,
        *,
        author: str,
        quote: str = "",
        note: str = "",
        kind: str = "note",
    ) -> dict:
        self.get_chunk(book_id, chunk_id)
        if author not in {"human", "ai"}:
            raise ReadingLibraryError("笔记作者必须是 human 或 ai")
        quote = str(quote or "").strip()[:6000]
        note = str(note or "").strip()[:6000]
        if not quote and not note:
            raise ReadingLibraryError("划线或笔记至少填写一项")
        if kind not in {"highlight", "note", "question", "insight"}:
            kind = "note"
        item = {
            "id": uuid.uuid4().hex[:12],
            "chunk": int(chunk_id),
            "author": author,
            "kind": kind,
            "quote": quote,
            "note": note,
            "created_at": _now(),
        }
        with self._lock:
            path = self._path(book_id, "annotations.json")
            annotations = self._read_json(path, [])
            annotations.append(item)
            self._write_json(path, annotations)
        return item

    def delete_annotation(self, book_id: str, annotation_id: str) -> None:
        with self._lock:
            path = self._path(book_id, "annotations.json")
            annotations = self._read_json(path, [])
            kept = [item for item in annotations if item.get("id") != annotation_id]
            if len(kept) == len(annotations):
                raise ReadingLibraryError("未找到这条笔记")
            self._write_json(path, kept)

    def store_analysis(self, book_id: str, chunk_id: int, analysis: dict) -> dict:
        chunks = self._chunks(book_id)
        chunk_id = int(chunk_id)
        if chunk_id < 0 or chunk_id >= len(chunks):
            raise ReadingLibraryError("阅读位置超出范围")
        clean = {
            "summary": str(analysis.get("summary") or "").strip()[:2000],
            "themes": [str(item).strip()[:100] for item in (analysis.get("themes") or [])[:8] if str(item).strip()],
            "questions": [str(item).strip()[:300] for item in (analysis.get("questions") or [])[:5] if str(item).strip()],
            "model_generated": True,
            "updated_at": _now(),
        }
        with self._lock:
            chunks[chunk_id]["analysis"] = clean
            self._write_json(self._path(book_id, "chunks.json"), chunks)
        return clean

    def export_markdown(self, book_id: str) -> str:
        book = self.get_book(book_id)
        chunks = self._chunks(book_id)
        annotations = self._read_json(self._path(book_id, "annotations.json"), [])
        lines = [f"# {book['title']}", ""]
        if book.get("author"):
            lines.extend([f"Author: {book['author']}", ""])
        lines.extend([f"Human progress: {book['percent']}%", "", "## Shared notes", ""])
        for chunk in chunks:
            related = [item for item in annotations if int(item.get("chunk", -1)) == int(chunk["id"])]
            if not related:
                continue
            lines.extend([f"### {chunk['title']}", ""])
            for item in related:
                who = "Human" if item.get("author") == "human" else "AI"
                if item.get("quote"):
                    lines.extend([f"> {str(item['quote']).replace(chr(10), chr(10) + '> ')}", ""])
                if item.get("note"):
                    lines.extend([f"- **{who} · {item.get('kind', 'note')}**: {item['note']}", ""])
        return "\n".join(lines).rstrip() + "\n"

    def review_book(self, book_id: str) -> dict:
        """Return a discussion map without returning the whole copyrighted text."""
        book = self.get_book(book_id)
        chunks = self._chunks(book_id)
        annotations = self._read_json(self._path(book_id, "annotations.json"), [])
        sections = []
        for chunk in chunks:
            related = [
                {
                    **item,
                    "quote": str(item.get("quote") or "")[:1200],
                    "note": str(item.get("note") or "")[:1200],
                }
                for item in annotations
                if int(item.get("chunk", -1)) == int(chunk["id"])
            ]
            if chunk.get("analysis") or related:
                sections.append({
                    "chunk_id": chunk["id"],
                    "title": chunk["title"],
                    "analysis": chunk.get("analysis"),
                    "annotations": related,
                })
        return {"book": book, "sections": sections}

    def delete_book(self, book_id: str) -> None:
        directory = self._book_dir(book_id)
        if not os.path.isdir(directory):
            raise ReadingLibraryError("未找到这本书")
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            if os.path.isfile(path):
                os.unlink(path)
        os.rmdir(directory)
