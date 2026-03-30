---
name: syntheta-blueprint
description: Use this skill when planning or implementing architectural changes to Syntheta Hub, especially when evolving the system into a more advanced, more capable, more intelligent assistant without breaking current behavior. Covers the current system blueprint, subsystem boundaries, hard runtime contracts, and safe evolution strategy across Python brain, Go bridge, memory, routing, satellites, and web UI.
---

# Syntheta Blueprint

Treat the current repo as a live blueprint, not a blank slate.

## What Syntheta is today

Syntheta is a local-first orchestration system with four stable layers:

- Boot and supervision: `setup.sh`, `run_syntheta.sh`, `launcher.py`
- Cognitive core: `python/main.py`, `python/services/engine.py`, `python/core/*`, `python/nlu/*`
- Real-time transport: `go/cmd/main.go`, `go/downlink/*`, `go/dispatcher/*`
- User surfaces: `python/services/web_api.py`, `python/web_gateway.py`, `webui/*`

Current intelligence is built from a hybrid of reflex logic, routed LLM calls, persistent memory, background research workers, and satellite I/O.

## Hard architectural contracts

Preserve these unless the task explicitly requires a coordinated redesign:

- Go owns low-latency audio relay on `5555` and the event dispatcher on `9001`.
- Python owns control-plane logic on `5556`, UDP audio ingest on `6000`, discovery on `6002`, UI server on `8000`, and gateway broadcast on `8001`.
- `launcher.py` is the supervisor and restart boundary. Exit code `42` is an intentional restart path.
- Ollama is the local model backend on `localhost:11434`.
- Durable knowledge is split between SQLite in `assets/database/syntheta_ledger.db` and vault JSON files under `assets/knowledge`.
- The current system is offline-first by default. Do not introduce mandatory cloud dependencies into core paths.

## Evolution goals

When improving Syntheta, optimize for these outcomes:

- More intelligence without slower wake-to-response latency
- Better memory quality without storing noisy or transient facts
- Richer multimodal and agentic behavior without breaking the current ports, process model, or UI event contract
- Better modularity so new capabilities can be added behind existing interfaces

## Safe change strategy

Use progressive evolution instead of broad rewrites:

1. Identify the current contract first: process boundary, function entrypoint, data store, event schema, port, or file format.
2. Extend behind the contract before replacing it.
3. Keep old and new paths compatible during migration.
4. Verify both immediate behavior and background side effects.
5. Only collapse legacy paths after the replacement is proven stable.

## Where to extend intelligence

- Routing and cognition: `python/nlu/router_bridge.py`, `python/nlu/llm_bridge.py`, `python/core/pi_manager.py`
- Memory quality and recall: `python/services/memory_worker.py`, `python/core/context_assembler.py`, `python/core/database_manager.py`
- Execution and orchestration: `python/services/engine.py`
- Live research and enrichment: `python/services/idle_librarian.py`, `python/services/claw_worker.py`, `python/nlu/api_scout.py`
- Real-time interaction: `python/services/communications.py`, `python/audio/stt_event_emitter.py`, `go/*`, `webui/*`

## Rules for non-breaking evolution

- Do not merge Go and Python responsibilities casually; the split is performance-driven.
- Do not replace memory sanitization with naive persistence; voice systems generate low-quality facts under load.
- Do not break websocket or injected event flows without updating both backend and frontend.
- Do not remove the current reflex layer just because a larger model seems smarter; it exists for latency and reliability.
- Prefer additive schemas, additive payloads, and feature flags over destructive changes.

## Definition of a good Syntheta change

A good change makes Syntheta more capable while preserving bootability, port ownership, local operation, memory integrity, and basic interaction reliability.
