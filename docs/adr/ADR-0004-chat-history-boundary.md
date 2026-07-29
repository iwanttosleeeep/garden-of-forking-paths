# ADR-0004: Chat History remains an explicit document library

## Decision

Uploaded Chat History files remain private Markdown documents outside Memos. The MCP `recall(title)` tool requires an exact title or filename and returns only that document, capped at 30,000 characters.

## Why this is not cognition

The library preserves user-supplied transcripts. It does not infer beliefs, preferences, goals, or instructions from them.

## Why this is not a database feature

It is a small file library with editable display metadata, not a general query or indexing surface.

## How forgetting still works

The user can delete an uploaded document completely from the Chat History page without affecting Memos.

## How tombstones are preserved

Chat documents are outside the memo lifecycle. Their explicit hard-delete action therefore creates no hidden memo or archive duplicate.

## How present thinking remains with the LLM

The LLM receives one explicitly named transcript as context and performs all current reasoning itself.

## Rejected alternatives

Automatic transcript search, inclusion in Breath, and ingestion into Memos were rejected to prevent unsolicited context and accidental duplication.

## Tests required

Cover Markdown-only uploads, path safety, exact-title selection, duplicate-title handling, response bounds, metadata edits, deletion, and absence from Memo retrieval.
