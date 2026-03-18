from typing import TypedDict, Dict, List, Optional, Any, Callable


# ============================================================
# GOLDEN PACKET — single source of truth for every LLM call
# Built by StateManager.build_golden_packet()
# Enriched by LibrarianRouter.enrich_packet()
# Consumed by OllamaBridge.generate()
# ============================================================
class GoldenPacket(TypedDict, total=False):
    # ── Identity ──────────────────────────────────────────────
    role:    str        # System persona prompt

    # ── Session context ───────────────────────────────────────
    ctx:     str        # Topic classification (from router)
    emotion: str        # Detected user emotion

    # ── Extracted knowledge ───────────────────────────────────
    entities: Dict[str, Any]

    # ── Input ─────────────────────────────────────────────────
    input:   str        # Resolved user text (pronoun-resolved by router)

    # ── Conversation history ──────────────────────────────────
    # Sliding window (last 5 pairs) managed by engine
    # May be prefixed with 1B summary of older turns
    history: str

    # ── Memory layers ─────────────────────────────────────────
    # memory_context: real-time JSON bucket facts (current session)
    #   Written by RealtimeMemoryCapture background triage
    #   Retrieved by get_context_fast() — keyword scored, zero LLM
    memory_context: str

    # memory_tank: SQL nomic retrieval OR web_data
    #   Set by engine._handle_normal_command() after enrich_packet()
    #   If route=web: contains SearxNG summary
    #   If route=memory/general: contains top-k SQL cosine facts
    memory_tank: str

    # ── Routing (set by LibrarianRouter.enrich_packet()) ──────
    route_taken:         str   # general_no_web | general_web_search | sql_metrics
    needs_memory:        bool  # True → engine should inject personal context
    matched_memory_node: Optional[str]   # e.g. "Devices::iPhone 12"
    web_data:            Optional[str]   # Raw SearxNG synthesis output

    # ── Model selection ───────────────────────────────────────
    # Default: mistral:7b
    # NightWatchman sets "mistral:7b" for biographer calls
    model: str

    # ── Abort check ───────────────────────────────────────────
    # Lambda set by engine: lambda: state.session_start_time != current_session_id
    # OllamaBridge checks this during streaming to kill on barge-in
    abort_check: Optional[Callable[[], bool]]


# ============================================================
# COGNITIVE STATE — per-satellite persistent brain state
# Stored in EngineState.cognitive[sat_id]
# ============================================================
class CognitiveState(TypedDict, total=False):
    topic:    str                       # Current topic label
    entities: Dict[str, Any]            # Extracted named entities

    # Live conversation turns — [{role, content}, ...]
    # Engine maintains sliding window of last 8 pairs
    history_buffer: List[Dict[str, str]]

    # Compressed summary of older turns (written by 1B summarizer)
    # Prepended to history_str when building GoldenPacket
    summary: str

    last_interaction: float             # Epoch time of last user turn
    active_subject:   str               # Last topic for resume prompts
    is_active:        bool