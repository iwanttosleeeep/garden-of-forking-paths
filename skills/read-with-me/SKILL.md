---
name: read-with-me
description: Read books together through Garden with honest shared progress, chunk-by-chunk discussion, highlights, notes, and whole-book reflection. Use when the user asks to start or continue a Garden book, says 陪我读/一起读/继续读, wants to compare reading progress, discuss a passage or completed book, or bring selected WeRead highlights into a shared reading conversation.
---

# Read With Me

Read only through the Garden `read_book` MCP tool. Keep source books outside Memos and never imply that a chunk was read before opening it.

## Start or resume

1. Call `read_book(action="library")` when the book is not unambiguous.
2. Ask the user to upload EPUB, TXT, or Markdown in Garden → Reading when the shelf is empty. Do not request the private book file in chat.
3. Call `read_book(action="open", book_id=...)` without `chunk_id` to join the human at their current position.
4. State the section and shared progress briefly. Discuss the actual returned passage instead of giving a generic summary.

## Read together

- Open one chunk at a time. Use `open` for the next chunk only when the conversation reaches it.
- Treat cached DeepSeek analysis as a reading map, not as source text.
- Prefer one attentive observation and one genuine question over a long lecture.
- Distinguish the user's highlights from AI notes. Never attribute an AI interpretation to the user.
- Call `read_book(action="note", ...)` only after the user explicitly asks to retain the AI highlight, question, or insight.
- Use `read_book(action="progress", ...)` only to correct or inspect AI progress; `open` already advances it honestly.

If the WeRead skill is available, use it only to fetch the user's selected progress, highlights, or notes. Do not copy an entire commercial book through WeRead. Keep imported WeRead material attributed to the user and discuss it alongside the matching Garden chunk.

## Close a session

Summarize what changed in two or three sentences. Ask which shared insight, if any, should become a Garden Memo. Call `hold` only after explicit confirmation; reading notes themselves belong in Reading.

## Finish a book

1. Call `read_book(action="review", book_id=...)` to obtain cached reading maps and shared margins without loading the full book.
2. Build the final discussion from those records and open individual chunks only when exact wording matters.
3. Call `read_book(action="finish", book_id=...)` only after actually completing the remaining reading.
4. Offer to export the shared margins as Markdown from Garden → Reading.
