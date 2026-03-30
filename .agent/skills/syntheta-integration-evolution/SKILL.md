---
name: syntheta-integration-evolution
description: Use this skill when evolving Syntheta's real-time interfaces and integration surfaces: Go audio bridge, satellite transport, playback events, FastAPI/websocket UI, web gateway broadcasts, or Home Assistant/device execution. It guides Antigravity to add capability without breaking the transport contracts and event flows already encoded in syntheta-hub.
---

# Syntheta Integration Evolution

Use this skill for transport, device, and UI evolution.

## Current interface map

- Go bridge starts in `go/cmd/main.go`
- Satellite audio enters Go on UDP `5555`
- Python receives forwarded audio on UDP `6000`
- Python satellite control uses TCP `5556`
- Discovery uses UDP `6002`
- Python emits playback and audio events to Go on TCP `9001`
- FastAPI UI runs on `8000`
- Persistent gateway broadcast path runs on `8001`
- Local UI injector listens on `9002`

These interfaces are the current nervous system of Syntheta. Preserve them unless the task explicitly includes a migration plan.

## Ownership boundaries

- Go is the low-latency transport and audio side.
- Python is the stateful orchestration and decision side.
- Web UI is a consumer and initiator of events, not the orchestration source of truth.

## Safe evolution patterns

- Add fields to JSON payloads instead of changing required ones in place.
- Keep backend and frontend event names aligned.
- Extend the Python-Go handshake rather than replacing it suddenly.
- If introducing new satellites or sensors, integrate through existing control and event paths first.
- Keep UI changes tolerant of delayed or missing events; the runtime is distributed and asynchronous.

## Good targets for improvement

- Better event observability and structured telemetry
- Stronger reconnect behavior for satellites
- More explicit schemas for websocket and dispatcher payloads
- Cleaner playback lifecycle handling
- More capable browser controls and monitoring without bypassing engine logic
- Safer Home Assistant execution flows and richer action feedback

## Anti-patterns

- Moving command orchestration into the frontend
- Letting Go own business logic that already belongs to Python state
- Changing ports or event names without a staged compatibility plan
- Tying core execution to a browser session being present

## Validation

For integration work, verify end-to-end behavior across the full chain:

- source event
- transport boundary
- engine reaction
- emitted response
- UI or satellite acknowledgement

If only one side is updated, assume the change is incomplete.
