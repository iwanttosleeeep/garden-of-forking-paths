# Signal Hut / 传声小屋

`/voice` is a standalone Moonrise-style page, linked from the map and dashboard.
Lake blue (`#576F84`), linen, and terracotta distinguish it from Radio and Reading.
It is a public static UI, like the map: it exposes no Garden data and changes no
OAuth, memory, retrieval, or decay logic. It never writes to Memos.

## Data path

The browser explicitly connects to **http://127.0.0.1:8765**, never a configurable
URL. The existing StackChan Voice Lab must run on that same computer. Clicking
Connect requests browser local-network access; loading the page does not probe
the network. Capture starts only after a recording click. USB audio and browser
recordings go to the local Python service for SenseVoice/Whisper/acoustics, not
to Garden. Corrected text and a bounded sound summary go to the existing
StackChan relay only after the explicit consent checkbox and Send click.

The local service must report `app: stackchan-voice-lab` and
`bridge_protocol: garden-voice-v1`. It permits precisely the production Garden
origin `https://moonrisekingdom.sehnsucht.uk`, an endpoint/method allowlist, and
the `X-Garden-Voice: 1` header. No wildcard CORS, browser-stored credentials,
forwarded Garden cookies, redirects, or microphone auto-start. The service
continues binding only to loopback, with its Host/origin checks intact.

The existing relay intentionally permits unauthenticated latest-message pickup:
new messages replace old ones, reading consumes one, and unread messages expire
after ten minutes. Upload authorization stays server-side on the Mac. This page
does not strengthen that relay boundary, claim who read a message, or erase a
message already in a Claude conversation. Avoid sensitive content.

## Deployment / usage

1. Update the Mac Voice Lab `server.py` with the Garden bridge version and restart
   the service. It can run in the background; its original browser tab is optional.
2. Deploy this Garden commit using the existing **git + rebuild** process. No hot
   copying into a container; check `git log --oneline -1` before rebuilding.
3. On that Mac, open `/voice`, click Connect, allow browser local-network access,
   then use either USB capture or the computer microphone. Desktop Chrome is the
   primary validation target. Browser microphone permission is separate.
4. A phone cannot use the Mac service through its own 127.0.0.1. The page explains
   this, offline startup, old bridge versions, and denied permissions. It does not
   install a login item itself. If the Mac companion has a user-approved login
   agent, it starts after login; otherwise the service must be started manually.

Only `/voice` overrides the default microphone-deny header, retaining camera,
USB, geolocation, payment denial and frame restrictions. Its CSP permits exactly
the fixed loopback API and same-origin static assets. No remote fonts/scripts.

Browser permission background: [Chrome Local Network Access](https://developer.chrome.com/blog/local-network-access).
No browser security flags need to be disabled.

## Shared Voice Lab snapshot

`voice-app.js`, `voice-delivery.js`, and `voice-endpoint.js` are a reviewed snapshot
of StackChan `tools/voice-lab/{app,delivery,endpoint}.js` as of 2026-10-04. Only
`fetch` → `voiceFetch` transport substitution, status polling scheduling, and a
disconnect hook differ (interval permits an immediate refresh after Connect
without duplicate timers; disconnection stops microphone tracks and invalidates
any pending permission grant before disabling the workspace).
They retain sticky transcription edits, original model comparison, manual tone
correction, inference generation guards, send consent invalidation, uncertain
delivery receipts, and ID-scoped withdrawal. Future updates must review this
snapshot together with the local implementation; it is not a runtime CDN import.

Tests: `tests/test_voice_page.py`, `tests/frontend/voice-bridge.test.cjs`, and the
StackChan Voice Lab bridge/UI regression tests. Use synthetic audio and mock all
publish/withdraw requests; never consume or replace an existing relay message
as a test. No models, recordings, credentials, or private data belong in this repo.
