# ADR-0002: Journal imports require an explicit retrieval boundary

## Decision

Sterling journals enter the Garden only through a user-selected JSON export. Each entry is stored as a normal Markdown memo with `source_tool: sterling` and `dont_surface: true`. The MCP `journal` tool returns only a trend summary by default and reveals journal text only when the AI explicitly supplies a query.

## Why this is not cognition

Journal records are user-provided evidence. They do not create beliefs, goals, diagnoses, or autonomous interpretation.

## Why this is not a database feature

The import preserves a small personal record and a mood curve in the existing memo store. It does not introduce a general external-data synchronization service.

## How forgetting still works

Imported entries retain the existing archive and `dont_surface` controls. They are hidden from unsolicited surfacing, not made immutable.

## How tombstones are preserved

Deleting an imported entry follows the standard archive/tombstone path and preserves its original memo identifier.

## How present thinking remains with the LLM

The LLM can receive a bounded, explicitly requested slice of journal context. It performs all current reasoning itself and must not treat mood values as medical conclusions.

## Rejected alternatives

Directly reading Sterling browser localStorage was rejected because a server cannot safely or reliably access it. Automatic full-text inclusion in Breath was rejected because private diary content should never become unsolicited context.

## Tests required

Tests cover Sterling export parsing, stable-ID deduplication, `dont_surface` protection, mood-summary calculation, and the rule that raw journal text requires an explicit query.
