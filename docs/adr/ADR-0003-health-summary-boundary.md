# ADR-0003: Health daily-summary boundary

## Decision

Garden accepts a revocable, private iPhone sync key and stores only validated daily HealthKit summaries in `.health/daily_summaries.json`. It does not use GitHub, Sterling, or the memo store for Health data. Cycle data remains visible only on the Health page. No Health MCP tool is exposed and no Health record is automatically placed in LLM context.

## Why this is not cognition

The service stores measurements supplied by HealthKit; it does not diagnose, score sleep, infer a cycle, or reason about wellbeing.

## Why this is not a database feature

This is a small private companion-data file with a fixed daily schema, not a general queryable collection or a new memory model.

## How forgetting still works

The companion sends replaceable daily snapshots. A future explicit erase control can delete a selected day or the whole Health file without touching memories.

## How tombstones are preserved

Health summaries are not memos and therefore never create memo tombstones. Existing memo deletion and tombstone rules remain unchanged.

## How present thinking remains with the LLM

Health data is intentionally excluded from automatic retrieval. Any future use in a conversation must be a separate, explicit user-controlled action.

## Rejected alternatives

Raw heart-rate streams, automatic GitHub backups, syncing through Sterling, and an exposed `health` MCP tool were rejected for privacy, complexity, and accidental-context reasons.

## Tests required

Cover bearer-key rejection, schema/range validation, bounded payloads, atomic daily replacement, dashboard authentication, and absence from MCP tool manifests.
