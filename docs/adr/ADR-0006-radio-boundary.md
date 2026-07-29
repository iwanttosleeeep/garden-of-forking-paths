# ADR-0006: Radio is an explicit playlist collaboration surface

## Decision

Garden delegates NetEase authentication and playlist operations to the official CLI. The MCP `radio` tool can list explicitly shared Ainsley playlists, read tracks, search, create Senn playlists, add tracks to Senn-owned playlists, and attach Senn comments. Mutating actions require explicit confirmation.

## Why this is not cognition

Recommendations and comments are current conversational output. They are not converted into personality, preference, or memory state.

## Why this is not a database feature

Garden retains only minimal connection markers, sharing choices, ownership, and comments needed for the playlist experience.

## How forgetting still works

Removing exposure or deleting Garden-side Radio metadata stops it from being offered to Senn without touching Memos.

## How tombstones are preserved

Radio records are outside the memo archive and do not modify memo tombstones.

## How present thinking remains with the LLM

Senn must explicitly call Radio for the requested playlist task and explains recommendations in the current turn or an intentional comment.

## Rejected alternatives

Browser audio playback, automatic access to all personal playlists, and sending Memos, journals, or chats to NetEase were rejected as unnecessary scope and privacy risk.

## Tests required

Cover identifier normalization, explicit exposure, owner-separated views, confirmation gates, Senn-only mutation checks, comment persistence, and the exact public tool manifest.
