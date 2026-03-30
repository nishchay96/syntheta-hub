---
name: syntheta-cognitive-evolution
description: Use this skill when extending Syntheta's intelligence layer: reasoning, memory, routing, context assembly, research workers, personalization, agentic behavior, or decision quality. It helps Antigravity evolve the Python brain into a more capable system while respecting the current execution model and avoiding regressions in latency, memory quality, and user-facing behavior.
---

# Syntheta Cognitive Evolution

Use this skill for changes inside the Python brain.

## Current cognitive stack

- `python/core/pi_manager.py`: fast reflex gating, confirmation flows, session handling
- `python/services/engine.py`: central orchestrator
- `python/nlu/router_bridge.py`: `web` vs `memory` vs `both` vs `general`
- `python/nlu/llm_bridge.py`: Ollama prompting and final answer generation
- `python/core/context_assembler.py`: exact-node and fallback memory assembly
- `python/services/memory_worker.py`: async memory capture, extraction, sanitation, persistence
- `python/services/idle_librarian.py` and `python/services/claw_worker.py`: background research and live enrichment

## Design intent

Syntheta is not one model call. It is a layered cognitive system.

- Reflex handles speed-critical intents.
- Router decides what kind of intelligence is needed.
- Context assembler retrieves targeted memory.
- LLM bridge produces the final natural-language answer.
- Background workers improve future answers without blocking the current one.

Preserve that layering. Improve it, but do not flatten it into a single opaque call path.

## How to evolve safely

- Add smarter routing before increasing prompt size everywhere.
- Improve memory precision before increasing memory volume.
- Add structured intermediate state before adding more prompt prose.
- Prefer better retrieval and better action planning over bigger monolithic prompts.
- Keep hot-path actions fast; move expensive enrichment into background workers when possible.

## Memory principles

- Durable memory must remain selective.
- Transient states, weak facts, and live-news fragments should not become long-term memory.
- If you add new memory types, wire the full lifecycle: capture, queue, sanitize, store, retrieve, and refresh router anchors.
- Respect the dual-store design: SQLite for queueing/indexed records and vault files for structured personal knowledge.

## Preferred future directions

- Better episodic memory and per-user timelines
- Smarter retrieval ranking over `Bucket_*.json` nodes
- More deliberate action planning before Home Assistant or tool execution
- Richer background synthesis from OpenClaw and live cache sources
- Improved multi-turn continuity without polluting prompts with stale context
- More robust confidence scoring and fallback behavior

## Anti-patterns

- Replacing the reflex path with LLM-only intent detection
- Storing every user utterance as memory
- Mixing live web snippets into permanent personal memory
- Expanding prompts instead of fixing routing or retrieval
- Making background workers block foreground response paths

## Validation

For cognitive changes, always check three things:

- Immediate answer quality
- Memory side effects after the response
- Failure behavior when web, models, or background workers are unavailable
