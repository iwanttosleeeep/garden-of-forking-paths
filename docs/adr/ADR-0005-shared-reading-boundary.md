# ADR-0005: Shared Reading keeps books and WeRead data outside Memos

## Decision

Garden stores imported reading material, chunk progress, highlights, and margins in its private Reading library. The MCP `read_book` tool exposes only explicit library, chunk, progress, note, review, finish, and WeRead actions. WeRead credentials stay in Garden configuration and commercial book text is not copied from WeRead.

## Why this is not cognition

Reading progress and notes are evidence from two readers. They do not create autonomous beliefs or decide what either reader should think.

## Why this is not a database feature

The feature is a bounded bookshelf workflow with stable book/chunk identities rather than a general storage API.

## How forgetting still works

Books and their companion records can be removed from Reading without creating or modifying Memos.

## How tombstones are preserved

Reading records do not participate in memo tombstones. Memo archival rules remain unchanged.

## How present thinking remains with the LLM

Senn reads one requested chunk or WeRead view at a time and supplies current-turn discussion; cached analysis is reference material only.

## Rejected alternatives

Copying the full WeRead catalogue into Garden, automatically surfacing book text, and writing reading data into Memos were rejected for copyright, privacy, and context-boundary reasons.

## Tests required

Cover safe EPUB parsing, chunk bounds, progress updates, note storage, credential secrecy, normalized WeRead responses, and absence from automatic Memo retrieval.
