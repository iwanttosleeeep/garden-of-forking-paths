# ADR-0003: Health daily-summary boundary

## Decision

Garden accepts a revocable, private iPhone sync key and stores only validated daily HealthKit summaries in `.health/daily_summaries.json`. It does not use GitHub, Sterling, or the memo store for Health data. Records are retained for at most 30 days. The explicit MCP `check_up(days)` reader can return a user-requested, bounded 1–30 day slice; no Health record is automatically placed in LLM context.

## Why this is not cognition

The service stores measurements supplied by HealthKit; it does not diagnose, score sleep, infer a cycle, or reason about wellbeing.

## Why this is not a database feature

This is a small private companion-data file with a fixed daily schema, not a general queryable collection or a new memory model.

## How forgetting still works

The companion sends replaceable daily snapshots. A future explicit erase control can delete a selected day or the whole Health file without touching memories.

## How tombstones are preserved

Health summaries are not memos and therefore never create memo tombstones. Existing memo deletion and tombstone rules remain unchanged.

## How present thinking remains with the LLM

Health data is intentionally excluded from automatic retrieval. Conversation use is a separate, explicit `check_up` action with a hard 30-day bound.

## Rejected alternatives

Raw heart-rate streams, automatic GitHub backups, syncing through Sterling, and unbounded or automatic Health retrieval were rejected for privacy, complexity, and accidental-context reasons.

## Tests required

Cover bearer-key rejection, schema/range validation, bounded payloads, atomic daily replacement, 30-day retention, dashboard authentication, explicit `check_up` manifest presence, and absence from automatic retrieval.
