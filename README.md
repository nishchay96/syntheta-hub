# Syntheta Hub

Syntheta Hub is an offline-first personal AI assistant stack built to run on local hardware. Its core design is not just about private inference; it is about continuity of memory. The system keeps a per-user profile, curates durable facts from interaction history, restores identity across sessions, and uses that memory to answer in a way that stays specific to the person using it.

The implementation combines a Python orchestration layer, a Go audio bridge, a browser UI, and local model serving through Ollama. Around that core, the codebase puts unusual emphasis on memory capture, profile continuity, interruption handling, and defensive controls that try to reduce weak or hallucinatory state from entering long-term memory.

## What The Codebase Is Optimized For

- Local execution through Ollama and local service orchestration rather than hosted inference by default.
- Personalized responses backed by per-user memory vaults, identity state, and memory context assembly.
- Continuous learning from interaction history through queued capture, background triage, and bucketed profile storage.
- Defensive memory handling with validation, sanitization, and rejection of transient or low-signal facts.
- Voice and web access paths that share the same assistant state instead of behaving like separate products.

## Personalization And Memory

The repository already contains a dedicated memory subsystem, `NightWatchman`, that captures interactions into `memory_queue`, curates them in the background, and syncs structured facts back into user-scoped storage. The current design includes:

- Per-user vault directories for persistent memory.
- Identity state tracking so the assistant knows which user profile is active.
- Context assembly that can surface profile facts quickly without waiting for fresh extraction.
- Background triage that routes facts into profile buckets such as `People`, `Work`, `Health`, `Devices`, and `Plans`.
- Idle-time processing so memory curation can continue without blocking the live response path.

In practical terms, Syntheta is meant to learn about the user over time instead of treating each conversation as stateless.

## Hallucination Control

The codebase clearly aims to suppress hallucination rather than accept it as normal behavior. That shows up in several places:

- A capture filter that rejects trivial, transient, and non-durable inputs before they become memory.
- Validation and sanitization stages in memory curation before facts are merged into persistent storage.
- Routing and API lookup paths that try to separate live-web questions from timeless or memory-backed questions.
- Guardrails for stale, weak, or low-signal facts so the assistant does not blindly store everything it hears.

This should be described as hallucination-resistant or memory-disciplined, not as an absolute guarantee that hallucinations are impossible.

## Architecture

- `python/`
  Core orchestration, routing, memory, LLM bridge, ASR, identity, and service logic.
- `go/`
  Audio bridge and low-level network/audio handling.
- `webui/`
  Browser interface for interacting with the assistant.
- `assets/`
  Runtime assets, system files, models, and generated data. Most of this is intentionally excluded from git.
- `launcher.py`
  Boot orchestration for the multi-process runtime.
- `run_syntheta.sh`
  Main entrypoint for starting the stack.

## Requirements

- Python 3.10+
- Go 1.20+
- Ollama installed and running locally
- Linux environment recommended for the current runtime scripts

## Quick Start

1. Clone the repository.
2. Run the setup script.
3. Configure environment variables.
4. Start the system.

```bash
chmod +x setup.sh
./setup.sh
```

If you need Home Assistant integration, create or update `.env` with the relevant values:

```bash
HA_URL=http://your-home-assistant.local:8123
HA_TOKEN=your_long_lived_access_token
```

Start the application with:

```bash
./run_syntheta.sh
```

## Development Notes

- `requirements.txt` contains the main Python dependencies.
- `requirements-audio.txt` contains the smaller audio-specific dependency set.
- Large model files and generated assets are not meant to be versioned in this repository.
- The codebase currently includes research and experimental subsystems; treat `tests/` as a mixed utility area rather than a polished public test suite.
- The implementation already includes user memory, identity persistence, idle-time curation, and response routing, so product-facing docs should describe those mechanisms directly rather than presenting Syntheta as a generic chatbot wrapper.

## Repository Hygiene

- Avoid committing model weights, generated databases, logs, and large binary artifacts.
- Prefer small, focused production commits over broad mixed changesets.
- Keep runtime secrets in `.env`, never in tracked files.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for branch, commit, and repository hygiene guidance.

## License

This project is released under the MIT License. See [LICENSE](LICENSE).
