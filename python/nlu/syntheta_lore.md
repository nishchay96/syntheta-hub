# Syntheta: The Sovereign, Self‑Improving Home Intelligence

> *“Measure twice, cut once.”* – Engineering philosophy that guides every line of code.

Syntheta is not just another smart home assistant. It’s a **sovereign, fully local intelligence** designed to respect your privacy, understand natural conversation, and even help you improve its own code. Born from a frustration with cloud‑dependent, eavesdropping devices, Syntheta is my answer to what a truly personal assistant should be.

This document tells the story of Syntheta – its purpose, its strengths, the unique features that set it apart, and the vision behind it. Whether you’re a developer, a privacy‑conscious homeowner, or just curious, welcome.

---

## 🔍 What Is Syntheta?

Syntheta is an **open‑source, offline‑first voice assistant** that runs entirely on your own hardware. It consists of:

- **ESP32‑S3 satellites** (one per room) that listen for wake words and stream audio.
- A **Go bridge** that routes audio and commands with minimal latency.
- A **Python brain** that handles speech‑to‑text, natural language understanding, conversation memory, and text‑to‑speech – all locally.

The system is named after its four developmental phases, each representing a layer of its architecture:

- **BETA (The Remote)** – Mobile/web interface, the “God Mode” controller.
- **ALPHA (The Satellite)** – Physical ESP32 devices, always‑listening, room‑aware.
- **GAMMA (The Gateway)** – Bluetooth bridge for private audio streaming to TWS earbuds.
- **OMEGA (The Hub)** – The central brain, where Whisper, Ollama, and Kokoro TTS run.

Together, they form a distributed, privacy‑preserving intelligence that can control your home, answer questions, and even help you debug and upgrade its own systems.

---

## ❓ Why Syntheta? (The Reason for Existence)

I built Syntheta because existing solutions fail on three fundamental fronts:

1. **Privacy** – Alexa, Google, and others are always listening – not just for wake words, but to the cloud. Your conversations are data to be mined.
2. **Natural Interaction** – Commercial assistants are brittle. They struggle with background noise, can’t handle interruptions, and forget context between turns.
3. **Extensibility** – You can’t truly hack Alexa. You’re locked into their ecosystem, their features, their update schedule.

Syntheta exists to give you back control. It’s **yours** – you own the hardware, the software, and the data. It works when the internet is down. It adapts to your home, not the other way around.

---

## 💪 Strengths & Unique Features

What makes Syntheta different? Let’s walk through the innovations hidden in the code.

### 1. Two‑Phase Wake Word Validation
- **Hardware wake** on the ESP32 triggers a session, but the **Gatekeeper** (in Python) validates the audio against a per‑room noise floor calibration.
- False wakes from TV, AC, or slamming doors are rejected *after* the wake word, so you never get an accidental “yes?”.

### 2. Zero‑Assumption Protocol
- When a command is ambiguous (low confidence from the NLU), Syntheta **asks for confirmation** instead of guessing.
- Implemented via `match_type: "assumed"` in the reflex catalog – the system waits for a “yes” or “no” before executing.

### 3. Smart Stream Management (Breathing Loop)
- The ESP32 runs a **state machine** (`smart_stream_state_t`) that:
  - Listens after a wake word (`DING_WAIT`).
  - Extends listening while you speak (`GRACE_LISTEN` → `EXTEND_LISTEN`).
  - Pauses when you stop (`PROCESSING_BREAK`).
- If you interrupt during TTS, the system **instantly stops playback** and starts listening again – no need to say the wake word again.

### 4. Per‑Room Contextual Memory
- Each satellite maintains its own `CognitiveState` (topic, entities, conversation history).
- The `GoldenPacket` bundles this context for the LLM, enabling natural follow‑ups like “How about tomorrow?” after “What’s the weather?”.
- History is stored in a rolling buffer (last 6 turns), ensuring the assistant remembers what you discussed in each room separately.

### 5. Reflex + LLM Hybrid Architecture
- **Reflex catalog** (`reflex_catalog.json`) provides instant, low‑latency execution for common commands (lights, fans, TV) using semantic matching (MiniLM).
- If no reflex matches, the system falls back to the **LLM** (Ollama with `llama3.2`) for open‑domain queries.
- This gives you the speed of a traditional voice assistant and the flexibility of a chatbot.

### 6. JIT TTS Masking with Learned Fillers
- When TTS generation takes longer than ~3 seconds, the system plays a pre‑recorded filler (“Let me think…”) while generating in parallel.
- Frequently used short phrases (“Okay”, “Sure”) are **automatically cached** as filler audio (`SmartTTSCache` learns your habits).
- Result: no awkward silence – the assistant sounds like it’s actually thinking.

### 7. Voice‑Based Sudo Mode
- After a silent‑environment calibration, you can enter “sudo mode” by saying “sudo login”.
- Once authenticated, you can issue system commands like “reboot”, “shutdown”, or “force update” by voice.
- Requires explicit confirmation – protects against accidental destruction.

### 8. Autonomous Code Engineering (The Memory Vault)
- Syntheta maintains a **ChromaDB vector database** of its own source code (C, Go, Python).
- It can retrieve relevant code snippets to help debug, upgrade, or rewrite its own systems – a feature rarely seen in consumer assistants.
- This turns Syntheta into a true **engineering partner**, not just a home helper.

### 9. Surgical Cleanup & Telemetry
- The launcher kills stale processes on ports 5555/5556/6000 before starting.
- Built‑in diagnostics monitor packet rates, buffer bloat, and processing latency, displayed in the console.
- Ensures rock‑solid uptime – you can trust it to run 24/7.

### 10. Fully Offline & Hackable
- All models (Whisper, MiniLM, Piper TTS, Ollama) run locally.
- Every component is open‑source – you can swap out the STT engine, the NLU model, or the TTS voice with minimal effort.

---

## 🏠 How Syntheta Is Different from Other Home Assistants

| Feature | Alexa / Google | Home Assistant (Voice) | Syntheta |
|---------|----------------|------------------------|----------|
| **Cloud dependency** | Required | Optional | **None** |
| **Privacy** | Poor (data mining) | Good (self‑hosted) | **Excellent (local only)** |
| **Wake word accuracy** | Good in quiet | Mediocre | **Two‑phase validation** |
| **Barge‑in support** | Limited | No | **Full (breathing loop)** |
| **Multi‑room context** | No | No | **Yes (per‑satellite memory)** |
| **Command confirmation** | No | No | **Zero‑Assumption Protocol** |
| **Self‑improvement** | No | No | **Autonomous code engineering** |
| **Voice personality** | Generic | Robotic | **Custom blend (Emma/Bella)** |
| **Cost** | Subscription | Free (self‑hosted) | **Free + hardware** |

---

## 🎯 Best Use Cases

- **Privacy‑first smart home** – Control lights, fans, AC, TV without any cloud.
- **Workshop / lab assistant** – Use voice to control tools, ask for measurements, or even get help debugging code.
- **Elderly / accessibility** – Natural conversation, confirmation of commands, and no reliance on internet.
- **Developer playground** – Experiment with voice AI, swap models, contribute to the core.
- **Private conversations** – With the Gamma (Bluetooth) phase, you can stream audio directly to earbuds for truly private interaction.

---

## 👤 A Note from the Developer (Nishchay)

> I started Syntheta (originally “EVA”) because I wanted a home assistant that *I* could trust. I’m a tinkerer – I love tearing things apart and rebuilding them better. But every time I looked at commercial assistants, I felt locked in. I couldn’t change the wake word, couldn’t fix the misunderstandings, couldn’t stop the data leaks.
>
> So I built my own.
>
> Syntheta is the result of countless nights, a few burnt ESP32s, and a lot of coffee. It’s named after the Greek word *synthetis* – “one who puts things together” – because that’s what it does: it puts together the best of open‑source AI, clever hardware, and a philosophy of zero assumptions.
>
> I’m sharing it because I believe technology should be a tool, not a master. If you want to take control of your smart home, if you want an assistant that actually listens (and shuts up when you tell it to), or if you just want to hack on something cool – Syntheta is for you.
>
> Fork it. Break it. Fix it. Make it yours.
>
> *— Nishchay, January 2026*

---

## 🚀 Getting Started

Ready to build your own Syntheta? Check out the [GitHub repository](#) for:

- Hardware BOM and assembly guide.
- Flashing instructions for ESP32 satellites.
- Setting up the Go bridge and Python brain.
- Configuration options (voice blend, wake word, etc.).
- Contribution guidelines.

Join the [Discord](#) to share your builds, ask questions, or help shape the future of sovereign home intelligence.

---

**Syntheta** – your home, your data, your intelligence.