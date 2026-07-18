# ADR-0001: Memory records remain evidence, not cognition

## Decision

User-facing language calls stored records “备忘录” (memos). Internal code, routes, file names, and compatibility fields retain their existing `bucket` names.

## Why this is not cognition

Memos preserve supplied text and metadata. They do not form beliefs, goals, or autonomous reasoning.

## Why this is not a database feature

The storage model exists to preserve memory traces and their history, rather than to expose a general-purpose data store.

## How forgetting still works

Decay, archiving, and `dont_surface` continue to control whether a memo is surfaced. Changing terminology does not alter those behaviors.

## How tombstones are preserved

Deletion remains archival rather than physical erasure. Existing archive records and their identifiers remain intact.

## How present thinking remains with the LLM

The LLM performs current-turn reasoning. Memos only provide retrieved context and never replace the model’s current reasoning.

## Rejected alternatives

Renaming `bucket` throughout implementation code was rejected because it would break public routes, stored data compatibility, and integrations without changing behavior.

## Tests required

Dashboard text regressions must preserve implementation identifiers while presenting the memo terminology. Existing storage, retrieval, archive, and API regression tests must continue to pass.
