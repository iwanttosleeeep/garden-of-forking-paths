import io
import json
import zipfile

import pytest

from reading_library import ReadingLibrary, ReadingLibraryError
from tools import _runtime as rt
from tools import reading as reading_tool


def _epub() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
            <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
              <rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles>
            </container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0"?>
            <package xmlns="http://www.idpf.org/2007/opf">
              <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
                <dc:title>Moon Book</dc:title><dc:creator>Suzy</dc:creator>
              </metadata>
              <manifest><item id="c1" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>
              <spine><itemref idref="c1"/></spine>
            </package>""",
        )
        archive.writestr("OEBPS/chapter.xhtml", "<html><body><h1>Arrival</h1><p>The tide came in.</p><p>We opened the map.</p></body></html>")
    return buffer.getvalue()


def _epub_with_unsafe_container_xml() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
            <!DOCTYPE container [<!ENTITY unsafe SYSTEM "file:///etc/passwd">]>
            <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
              <rootfiles><rootfile full-path="&unsafe;"/></rootfiles>
            </container>""",
        )
    return buffer.getvalue()


def test_text_book_keeps_progress_and_shared_annotations(tmp_path):
    library = ReadingLibrary(str(tmp_path))
    text = "# One\n\nFirst idea.\n\n# Two\n\nSecond idea."

    book = library.import_book("notes.md", text.encode(), author="Reader")
    assert book["title"] == "notes"
    assert book["author"] == "Reader"
    assert book["chunk_count"] == 2
    source_files = list((tmp_path / ".reading_library" / book["id"]).glob("source.*"))
    assert [path.name for path in source_files] == ["source.book"]
    assert not list((tmp_path / ".reading_library").rglob("*.md"))

    library.update_progress(book["id"], "human", 1, 0.5)
    library.update_progress(book["id"], "ai", 0, 1.0)
    note = library.add_annotation(
        book["id"], 1, author="human", quote="Second idea.", note="Keep this", kind="highlight"
    )
    chunk = library.get_chunk(book["id"], 1)
    assert chunk["annotations"][0]["id"] == note["id"]
    assert library.get_book(book["id"])["progress"]["human"]["chunk"] == 1
    assert "Keep this" in library.export_markdown(book["id"])
    review = library.review_book(book["id"])
    assert review["sections"][0]["annotations"][0]["note"] == "Keep this"

    duplicate = library.import_book("copy.md", text.encode())
    assert duplicate["id"] == book["id"]
    assert duplicate["duplicate"] is True

    library.delete_annotation(book["id"], note["id"])
    assert library.get_chunk(book["id"], 1)["annotations"] == []
    library.delete_book(book["id"])
    assert library.list_books() == []


def test_epub_uses_spine_order_and_metadata(tmp_path):
    library = ReadingLibrary(str(tmp_path))
    book = library.import_book("moon.epub", _epub())

    assert book["title"] == "Moon Book"
    assert book["author"] == "Suzy"
    chunk = library.get_chunk(book["id"], 0)
    assert chunk["title"] == "Arrival"
    assert "The tide came in." in chunk["text"]


def test_epub_rejects_external_xml_entities(tmp_path):
    library = ReadingLibrary(str(tmp_path))

    with pytest.raises(ReadingLibraryError, match="EPUB 目录结构无法解析"):
        library.import_book("unsafe.epub", _epub_with_unsafe_container_xml())


def test_rejects_unsupported_or_oversized_positions(tmp_path):
    library = ReadingLibrary(str(tmp_path))
    with pytest.raises(ReadingLibraryError, match="只支持"):
        library.import_book("book.pdf", b"pdf")
    book = library.import_book("book.txt", b"hello world")
    with pytest.raises(ReadingLibraryError, match="超出范围"):
        library.get_chunk(book["id"], 99)


def test_markdown_preamble_is_not_lost(tmp_path):
    library = ReadingLibrary(str(tmp_path))
    book = library.import_book("book.md", "A dedication.\n\n# Chapter\n\nThe story.".encode())

    assert book["chunk_count"] == 2
    assert library.get_chunk(book["id"], 0)["text"] == "A dedication."
    assert library.get_chunk(book["id"], 1)["text"] == "The story."


@pytest.mark.asyncio
async def test_single_read_book_tool_lists_opens_and_notes(tmp_path, monkeypatch):
    library = ReadingLibrary(str(tmp_path))
    book = library.import_book("book.txt", "A shared paragraph.".encode())
    monkeypatch.setattr(rt, "config", {"buckets_dir": str(tmp_path)})

    listing = await reading_tool.dispatch("library")
    opened = json.loads(await reading_tool.dispatch("open", book_id=book["id"], chunk_id=0))
    saved = await reading_tool.dispatch(
        "note", book_id=book["id"], chunk_id=0, quote="shared", note="Our thought", kind="insight"
    )

    assert book["id"] in listing
    assert opened["text"] == "A shared paragraph."
    assert "insight" in saved
    assert library.get_book(book["id"])["progress"]["ai"]["offset"] == 1.0


@pytest.mark.asyncio
async def test_read_book_tool_includes_weread_actions(tmp_path, monkeypatch):
    monkeypatch.setattr(rt, "config", {"buckets_dir": str(tmp_path), "weread": {"api_key": "wrk-test"}})

    async def fake_gateway(_config, api_name, **_params):
        if api_name == "/user/notebooks":
            return {
                "books": [{
                    "bookId": "book-1",
                    "book": {"title": "WeRead Book", "author": "Reader"},
                    "reviewCount": 1,
                    "noteCount": 2,
                    "bookmarkCount": 3,
                }]
            }
        if api_name == "/book/bookmarklist":
            return {"book": {"bookId": "book-1", "title": "WeRead Book"}, "updated": [{"markText": "A line"}]}
        if api_name == "/review/list/mine":
            return {"reviews": [{"review": {"content": "A thought"}}]}
        raise AssertionError(api_name)

    monkeypatch.setattr(reading_tool, "gateway_call", fake_gateway)
    notebooks = json.loads((await reading_tool.dispatch("weread_notebooks")).split("\n", 1)[1])
    notes = json.loads((await reading_tool.dispatch("weread_notes", book_id="book-1")).split("\n", 1)[1])

    assert notebooks["books"][0]["total_note_count"] == 6
    assert notes["highlights"][0]["text"] == "A line"
    assert notes["thoughts"][0]["content"] == "A thought"
