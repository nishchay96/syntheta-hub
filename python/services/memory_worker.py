import os
import time
import json
import logging
import threading
import glob
import re
import urllib.request
import requests
from datetime import datetime

from core.database_manager import DatabaseManager
from nlu.llm_bridge import OllamaBridge

logger = logging.getLogger("NightWatchman")

# ============================================================
# CONSTANTS — ported directly from finalized tester script
# ============================================================
STRUCTURAL_KEYS = {
    "status", "issue", "model", "color", "colour", "type", "category",
    "charger", "mentioned", "new issue", "new charger", "practices",
    "hobby", "action", "description", "date", "updated", "accessories",
    "storage", "capacity", "spending", "location", "purpose", "career",
    "employment", "detail", "note", "info", "data", "entry", "item",
    "speaker", "user", "person", "subject", "owner", "driver",
    "intent", "vehicle", "brand", "carstatus", "transmission",
    # Extra: transient physical state attrs — never store these
    "missing", "presence", "absent", "available", "nearby",
}

ALWAYS_CREATE_BUCKETS = {"Opinions"}

# First-person career/work signals
SELF_SIGNALS = [
    r'^i work\b', r'^i got (promoted|hired|fired|an offer)',
    r'^my office\b', r'^my salary\b', r'^my company\b', r'^my boss\b',
    r'^i am (thinking of|considering) (switching|changing|leaving)',
    r'^i received (an offer|a promotion)',
    r'^i joined\b', r'^i resigned\b', r'^i quit (my job|work)\b',
]

# Named person intent — stays in People, never moves to target bucket
PERSON_INTENT_PATTERN = (
    r'\b(priya|wife|husband|mother|father|friend|brother|sister)\b'
    r'.*(want|plan|going to|thinking of|consider|wish|need|prefer)'
)

# Implied attribute patterns — signals input is about last_entity
IMPLIED_ATTRIBUTE_PATTERNS = [
    r'^the (storage|screen|battery|camera|display|speaker|mic|keyboard|charger|ram|processor)',
    r'^its (storage|screen|battery|camera|display|speaker)',
    r'^(storage|battery|screen|ram|display) is ',
    r'upgraded (to|the) \d',
    r'(repaired|fixed|serviced|replaced) (it|the)',
]

BUCKET_ALIASES = {
    "diet": "Food", "nutrition": "Food", "eating": "Food",
    "technology": "Devices", "gadgets": "Devices", "electronics": "Devices",
    "automobiles": "Vehicles", "cars": "Vehicles", "transport": "Vehicles",
    "music": "Hobbies", "instruments": "Hobbies", "sports": "Hobbies",
    "tools": "Vehicles", "breakdowns": "Vehicles",
    "preferences": "Opinions", "interests": "Hobbies",
    "miscellaneous": "General",
}

NODE_STOPWORDS = {
    "hi", "hey", "hello", "okay", "ok", "yes", "no", "stop",
    "thanks", "thank", "please", "sorry", "what", "how", "why",
    "when", "where", "who", "which", "this", "that", "these",
    "there", "here", "now", "then", "just", "also", "well",
}

# Transient physical-state patterns — never store these as permanent facts
TRANSIENT_PATTERNS = [
    r"(don't|dont|do not|didn't|didnt) have .+ (with me|right now|today|on me)",
    r"(i feel|feeling) (sad|happy|tired|angry|excited)",
    r"(missing|not found|can't find|lost) (my|the)",
    r"(ran out of|out of|no more)",
]

OLLAMA_CHAT_URL  = "http://localhost:11434/api/chat"
OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"
TRIAGE_MODEL     = "llama3.2:3b"
NOMIC_MODEL      = "nomic-embed-text:v1.5"


# ============================================================
# REALTIME CAPTURE LAYER — called from engine hot path
# ============================================================
class RealtimeMemoryCapture:
    """
    Hot-path layer:
      1. Queue interaction to memory_queue (one SQL write, instant)
      2. Run full 4-stage triage pipeline in background thread

    Pipeline (background, never blocks LLM response):
      Stage 1 — Triage: bucket routing
      Stage 2 — Create/Merge: structured node extraction
      Stage 3 — Validate: hallucination gate
      Stage 4 — Guard: restore dropped nodes

    get_context_fast() reads existing JSON files — zero LLM calls.
    """

    def __init__(self, db_manager, vault_path, on_memory_changed=None):
        self.db         = db_manager
        self.vault_path = vault_path
        self.on_memory_changed = on_memory_changed
        self._user_dirs = {}        # {sat_id: str}
        self._last_entity = {}      # {sat_id: {"bucket": str, "node": str}}
        self.router     = None      # Wired by engine.__init__
        os.makedirs(vault_path, exist_ok=True)

    def user_exists(self, user_id: str) -> bool:
        """Checks if a vault directory exists for a given user name."""
        clean = re.sub(r'[^a-zA-Z0-9_]', '', user_id).lower().strip()
        if not clean: return False
        return os.path.exists(os.path.join(self.vault_path, clean))

    # ----------------------------------------------------------
    # VAULT
    # ----------------------------------------------------------
    def set_user(self, user_id: str, sat_id: int = 1):
        clean    = re.sub(r'[^a-zA-Z0-9_]', '', user_id).lower().strip() or "default"
        user_dir = os.path.join(self.vault_path, clean)
        os.makedirs(user_dir, exist_ok=True)
        self._user_dirs[sat_id] = user_dir
        logger.info(f"[Capture] Vault set for Sat {sat_id} → {user_dir}")

    def _get_user_dir(self, sat_id: int) -> str:
        if sat_id not in self._user_dirs:
            self.set_user(f"sat_{sat_id}", sat_id)
        return self._user_dirs[sat_id]

    # ----------------------------------------------------------
    # HOT-PATH ENTRY
    # ----------------------------------------------------------
    def capture(self, sat_id: int, user_text: str, llm_response: str = "", interaction_id = None):
        """Returns immediately. Triage runs in background thread."""
        if self._is_trivial(user_text):
            return
        # SQL queue write
        try:
            self.db.create_memory_task({
                "user_query":   user_text,
                "llm_response": llm_response,
                "sat_id":       sat_id,
                "timestamp":    datetime.now().isoformat()
            }, interaction_id=interaction_id)
        except Exception as e:
            logger.error(f"❌ [Capture] Queue write failed: {e}")

        # Background triage
        threading.Thread(
            target=self._background_triage,
            args=(sat_id, user_text, llm_response),
            daemon=True
        ).start()

    def _is_trivial(self, text: str) -> bool:
        t = text.strip().lower()
        if len(t.split()) <= 1:
            return True
        if t in NODE_STOPWORDS:
            return True
        # Pure general knowledge questions — no personal disclosure
        if t.startswith(("what is", "how does", "explain", "tell me about",
                          "what are", "who is", "when did", "where is",
                          "how do", "why does", "which is")):
            if not any(w in t for w in ["my ", "i have", "i own", "i like",
                                         "i hate", "i am ", "i work", "i use"]):
                return True
        # Transient physical states — never permanent
        for pattern in TRANSIENT_PATTERNS:
            if re.search(pattern, t):
                return True
        return False

    # ----------------------------------------------------------
    # BACKGROUND PIPELINE — full 4-stage pipeline from tester
    # ----------------------------------------------------------
    def _background_triage(self, sat_id: int, user_text: str, llm_response: str):
        logger.info(f"🧠 [Capture] Starting extraction thread for Sat {sat_id}...")
        user_dir = self._get_user_dir(sat_id)
        try:
            # Pre-process: resolve pronouns using last_entity context
            processed_text = self._preprocess(user_text, sat_id)
            logger.debug(f"🧠 [Capture] Preprocessed: '{user_text}' -> '{processed_text}'")

            # Stage 1: Triage — bucket routing
            facts = self._triage_fact(processed_text, user_dir)
            if not facts:
                logger.debug(f"🧠 [Capture] No facts extracted from: '{processed_text}'")
                return

            logger.info(f"🧠 [Capture] Extracted {len(facts)} facts. Updating ledger...")
            for bucket, summary in facts:
                # Resolve target node from entity context
                target = self._resolve_target(sat_id, bucket, summary)
                # Stage 2-4: Extract, validate, save
                self._update_ledger(sat_id, bucket, summary, user_dir, target)

        except Exception as e:
            logger.error(f"[Capture] Background triage failed: {e}", exc_info=True)

    # ----------------------------------------------------------
    # STAGE 0: PRE-PROCESS — pronoun resolution
    # Ported from tester._preprocess()
    # ----------------------------------------------------------
    def _preprocess(self, text: str, sat_id: int) -> str:
        t   = text.strip()
        ent = self._last_entity.get(sat_id)
        if not ent:
            return t

        entity = ent["node"]

        # Implied attribute patterns
        for pattern in IMPLIED_ATTRIBUTE_PATTERNS:
            if re.search(pattern, t.lower()):
                resolved = f"{entity}: {t}"
                logger.debug(f"[AttrInject] '{t}' → '{resolved}'")
                return resolved

        # Sentence-start pronouns
        start_pat = r'^(it|its|they|their|this|that|the thing|the device|the car|the bike|the phone)\b'
        if re.match(start_pat, t.lower()):
            resolved = re.sub(start_pat, entity, t, flags=re.IGNORECASE)
            logger.debug(f"[Pronoun:start] '{t}' → '{resolved}'")
            return resolved

        # Mid-sentence 'it'
        if re.search(r'\bit\b', t.lower()):
            resolved = re.sub(r'\bit\b', entity, t, flags=re.IGNORECASE)
            logger.debug(f"[Pronoun:mid] '{t}' → '{resolved}'")
            return resolved

        return t

    # ----------------------------------------------------------
    # STAGE 1: TRIAGE — bucket routing
    # Ported from tester.triage_fact()
    # ----------------------------------------------------------
    def _triage_fact(self, text: str, user_dir: str):
        existing = self._get_existing_buckets(user_dir)
        ctx_lines = []
        for b in existing:
            nodes = list(self._load_bucket(b, user_dir)["nodes"].keys())
            preview = ", ".join(nodes[:4]) if nodes else "empty"
            ctx_lines.append(f'  "{b}" → {preview}')
        ctx = "\n".join(ctx_lines) if ctx_lines else "  (none yet)"

        hints = []
        if self._is_self_fact(text):
            hints.append('SELF FACT: Speaker\'s own career. Route to "Work". NOT People.')
        if self._is_person_intent(text):
            hints.append('PERSON INTENT: Named person\'s want/plan → "People". NOT target domain.')
        hint_block = "\n".join(hints)

        prompt = f"""You are a memory router. Classify this into ONE bucket.

EXISTING BUCKETS:
{ctx}
{hint_block}

INPUT: "{text}"

HARD RULES:
- Named person facts → People
- Named person intent/want → People (not target domain)
- Speaker's career/work → Work
- Food preferences → Food
- Devices → Devices
- Vehicles → Vehicles
- Health → Health
- Opinions/likes/dislikes → Opinions
- TRANSIENT (do not store): physical absence ("don't have X with me"), 
  emotional state ("i feel sad"), current location — set is_permanent: false
- Two completely different domains in one sentence → two JSON objects.

fact_summary: one sentence, implicit subject, typos corrected.
Output raw JSON only.
{{"bucket": "Name", "fact_summary": "Concise corrected fact."}}"""

        raw_blocks = self._call_llm(prompt, enforce_json=True)
        if not raw_blocks:
            return None

        results = []
        for raw in raw_blocks:
            try:
                d = json.loads(raw)
                summary = d.get("fact_summary", text).strip()
                bucket  = d.get("bucket", "General").strip().title()
                
                if d.get("is_permanent") is False:
                    logger.info(f"🧠 [Triage] Skipping transient fact: {summary}")
                    continue
                
                if not summary:
                    continue
                
                bucket = self._normalize_bucket(bucket, existing)
                results.append((bucket, summary))
                logger.debug(f"🧠 [Triage] Routed '{summary}' -> {bucket}")
            except Exception as e:
                logger.error(f"⚠️ [Triage] Failed to parse block: {raw} | Error: {e}")
                pass

        return results if results else None

    def _normalize_bucket(self, proposed: str, existing: list) -> str:
        key = proposed.lower().replace(" ", "").replace("_", "")
        if key in BUCKET_ALIASES:
            canonical = BUCKET_ALIASES[key]
            if canonical in existing:
                return canonical
        for e in existing:
            if e.lower() == proposed.lower():
                return e
        return proposed

    def _is_self_fact(self, text: str) -> bool:
        return any(re.search(p, text.lower().strip()) for p in SELF_SIGNALS)

    def _is_person_intent(self, text: str) -> bool:
        return bool(re.search(PERSON_INTENT_PATTERN, text.lower()))

    # ----------------------------------------------------------
    # STAGE 2A: CREATE NODE (FEW-SHOT OPTIMIZED)
    # ----------------------------------------------------------
    def _create_node(self, fact_summary: str, bucket_name: str, current_date: str) -> dict:
        work_rule = ""
        if bucket_name == "Work":
            work_rule = '\nRULE: Node name MUST be "Self". No exceptions.'

        prompt = f"""Extract facts into a strictly formatted JSON memory node.

SENTENCE: "{fact_summary}"
BUCKET: {bucket_name}
DATE: {current_date}{work_rule}

RULES:
1. The JSON key MUST be the specific entity name (the device, food, person, or topic).
2. The value MUST be a dictionary of attributes.
3. NEVER store physical absence/presence as attrs (Missing, Presence, Absent).
4. Do not include the bucket name in the output.

EXAMPLES:
Input: "I like dark chocolate"
Output: {{"Dark Chocolate": {{"Preference": "Liked"}}}}

Input: "Wife is Priya"
Output: {{"Priya": {{"Relation": "Wife"}}}}

Input: "Works at Infosys"
Output: {{"Self": {{"Company": "Infosys"}}}}

Input: "I drive a red Toyota"
Output: {{"Toyota": {{"Color": "Red", "Status": "Owned"}}}}

Extract: "{fact_summary}"
Output ONE JSON object. Raw JSON only."""

        raw_blocks = self._call_llm(prompt, enforce_json=True)
        if not raw_blocks: return None
        raw_blocks.sort(key=len, reverse=True)
        for raw in raw_blocks:
            try:
                r = json.loads(raw)
                if isinstance(r, dict) and r: return r
            except Exception: continue
        return None

    # ----------------------------------------------------------
    # STAGE 2B: MERGE NODE (FEW-SHOT OPTIMIZED)
    # ----------------------------------------------------------
    # ----------------------------------------------------------
    # STAGE 2B: MERGE NODE — existing bucket
    # Ported from tester._merge_node()
    # ----------------------------------------------------------
    def _merge_node(self, existing_nodes: dict, fact_summary: str,
                    bucket_name: str, current_date: str,
                    target_node: str = None) -> dict:
        nodes_json = json.dumps(existing_nodes, indent=2)

        target_hint = ""
        if target_node and target_node in existing_nodes:
            target_hint = f'\nTARGET NODE: "{target_node}" — update this node if fact is about it.'

        # Bucket-specific merge rules
        scoped = ""
        if bucket_name == "Vehicles":
            scoped = ("\n- Node name = vehicle name. NEVER driver's name."
                      " Sold/replaced vehicle → mark old Status: Sold, create new node.")
        elif bucket_name == "Devices":
            scoped = ("\n- Each physical device = its own node."
                      " Node name = device name, not owner or action.")
        elif bucket_name == "Work":
            scoped = ("\n- ALL speaker career facts → 'Self' node."
                      " Company name is an attribute of Self, never a node.")
        elif bucket_name == "Opinions":
            scoped = "\n- Each distinct opinion topic = its own node."
        elif bucket_name == "People":
            scoped = ("\n- Each named person = their own node using their name."
                      " Person's intent/want = attribute of their node.")

        prompt = f"""Update a memory JSON with one new fact.

DATE: {current_date}
BUCKET: {bucket_name}
{target_hint}

EXISTING NODES:
{nodes_json}

NEW FACT: "{fact_summary}"

RULES:
1. Match fact to correct existing node by entity name.
   Different entity → create new node (spaces not underscores).
   Same entity → update that node only.
2. OVERWRITE conflicting keys. Never duplicate a key.
3. PRESERVE ALL existing nodes exactly.
4. Extract ONLY what is in NEW FACT. Do not invent.
5. Correct spelling silently.
6. NEVER add attrs: Missing, Presence, Absent, Available — these are transient states.{scoped}

Output the COMPLETE updated nodes dict. Raw JSON only."""

        raw_blocks = self._call_llm(prompt, enforce_json=True)
        if not raw_blocks:
            return None
        raw_blocks.sort(key=len, reverse=True)
        for raw in raw_blocks:
            try:
                r = json.loads(raw)
                if isinstance(r, dict) and r:
                    return r
            except Exception:
                continue
        return None

    # ----------------------------------------------------------
    # STAGE 3: VALIDATE — hallucination gate
    # Ported from tester._validate_nodes()
    # ----------------------------------------------------------
    def _validate_nodes(self, merged: dict, fact_summary: str,
                        existing_nodes: dict, bucket_name: str) -> dict:
        summary_lower = fact_summary.lower()
        existing_lower = {
            k.lower().replace("_", " "): k for k in existing_nodes.keys()
        }
        validated = {}

        for node, attrs in merged.items():
            node_norm = node.lower().replace("_", " ").strip()

            # Always keep existing nodes
            if node_norm in existing_lower:
                validated[existing_lower[node_norm]] = attrs
                continue

            # Self node always passes (bootstrapped by _ensure_self_node)
            if node_norm == "self":
                validated[node] = attrs
                continue

            # Reject structural keys as node names
            if node_norm.replace(" ", "") in STRUCTURAL_KEYS:
                logger.info(f"[Gate] Rejected structural key as node: '{node}'")
                continue

            # Reject NODE_STOPWORDS
            if node_norm in NODE_STOPWORDS:
                logger.info(f"[Gate] Rejected stopword node: '{node}'")
                continue

            # Grounding check — node name must appear in fact summary
            node_words = re.split(r'[_\s]+', node_norm)
            grounded = any(
                w in summary_lower for w in node_words if len(w) > 2
            )
            if grounded:
                # Also strip transient attrs from validated node
                if isinstance(attrs, dict):
                    clean_attrs = {
                        k: v for k, v in attrs.items()
                        if k.lower().replace(" ", "") not in STRUCTURAL_KEYS
                    }
                    validated[node] = clean_attrs if clean_attrs else attrs
                else:
                    validated[node] = attrs
            else:
                logger.info(f"[Gate] Rejected hallucinated node: '{node}'")

        # Fallback — never return empty
        if not validated:
            if existing_nodes:
                logger.info("[Gate] All new nodes rejected — preserving existing.")
                return existing_nodes
            else:
                return self._build_minimal_node(fact_summary, bucket_name)

        return validated

    def _build_minimal_node(self, fact_summary: str, bucket_name: str) -> dict:
        """Last-resort node when gate rejects everything."""
        if bucket_name == "Work":
            return {"Self": {"Detail": fact_summary[:80]}}
        words = fact_summary.split()
        candidates = [
            w.strip('.,') for w in words
            if w and w[0].isupper() and len(w) > 2
            and w.lower() not in {'the', 'a', 'an', 'is', 'at', 'in', 'on',
                                   'was', 'has', 'had', 'been', 'have', 'i'}
        ]
        node_name = candidates[-1] if candidates else bucket_name
        return {node_name: {"Status": "Noted"}}

    # ----------------------------------------------------------
    # STAGE 4: UPDATE LEDGER — orchestrates 2-4 + file write
    # Mirrors tester.update_ledger()
    # ----------------------------------------------------------
    def _ensure_self_node(self, bucket_name: str, user_dir: str):
        """Bootstraps Self node in Work bucket before LLM merge."""
        if bucket_name != "Work":
            return
        data = self._load_bucket("Work", user_dir)
        if "Self" not in data["nodes"]:
            data["nodes"]["Self"] = {}
            self._save_bucket(data, user_dir)
            logger.debug("[Self] Bootstrapped Self node in Work bucket.")

    def _resolve_target(self, sat_id: int, bucket: str,
                        summary: str) -> str:
        """Returns last entity's node name if it matches the current fact."""
        ent = self._last_entity.get(sat_id)
        if not ent or ent["bucket"] != bucket:
            return None
        node = ent["node"]
        node_words = re.split(r'[_\s]+', node.lower())
        if any(w in summary.lower() for w in node_words if len(w) > 2):
            return node
        return None

    def _set_last_entity(self, sat_id: int, bucket: str, node_name: str):
        """Tracks last meaningful entity for pronoun resolution."""
        norm = node_name.lower().replace("_", "").replace(" ", "")
        if norm in STRUCTURAL_KEYS or norm in NODE_STOPWORDS:
            return
        self._last_entity[sat_id] = {"bucket": bucket, "node": node_name}
        logger.debug(f"[Entity] Context → {bucket}::{node_name}")

    def _update_ledger(self, sat_id: int, bucket_name: str,
                       fact_summary: str, user_dir: str,
                       target_node: str = None):
        """Full stage 2-4 pipeline. Writes JSON + MD files."""
        # Bootstrap Self node for Work before LLM touches it
        self._ensure_self_node(bucket_name, user_dir)

        data      = self._load_bucket(bucket_name, user_dir)
        is_new    = not data["nodes"]
        snapshot  = dict(data["nodes"])
        cur_date  = datetime.now().strftime('%Y-%m-%d')

        if is_new:
            merged = self._create_node(fact_summary, bucket_name, cur_date)
        else:
            merged = self._merge_node(
                data["nodes"], fact_summary, bucket_name,
                cur_date, target_node
            )

        if not merged:
            logger.warning(f"🧠 [Ledger] LLM returned nothing for '{fact_summary[:50]}' in bucket {bucket_name}")
            return

        logger.debug(f"🧠 [Ledger] Merged nodes: {list(merged.keys())}")

        # Stage 3: validate
        merged = self._validate_nodes(
            merged, fact_summary, snapshot, bucket_name)

        # Stage 4: guard — restore any nodes dropped by LLM
        for node, attrs in snapshot.items():
            node_norm   = node.lower().replace("_", " ")
            merged_norms = {k.lower().replace("_", " ") for k in merged}
            if node_norm not in merged_norms:
                logger.debug(f"[Guard] Restoring dropped node: '{node}'")
                merged[node] = attrs

        data["nodes"] = merged
        self._save_bucket(data, user_dir)

        logger.info(f"📂 Bucket: {bucket_name} → node: "
                    f"{', '.join(merged.keys())}")

        # Update entity context
        for node_name in merged:
            norm = node_name.lower().replace("_", "").replace(" ", "")
            if norm not in STRUCTURAL_KEYS or norm == "self":
                self._set_last_entity(sat_id, bucket_name, node_name)
                break

        # Notify engine/router to refresh the UI matrix and personal anchor
        user_name = os.path.basename(os.path.normpath(user_dir))
        
        # 🟢 IMMEDIATE SQL SYNC
        for node_name, attrs in merged.items():
            if node_name.strip().lower() in NODE_STOPWORDS:
                continue

            sql_key = f"{bucket_name.lower()}.{node_name.lower().replace(' ', '_')}"
            
            # Create vector based on a readable string
            flat_text_for_vector = ", ".join(f"{k}: {v}" for k, v in attrs.items()) if isinstance(attrs, dict) else str(attrs)
            vector = self._get_nomic_vector(f"{node_name}: {flat_text_for_vector}")
            
            self.db.save_core_fact(
                user_id=user_name,
                bucket=bucket_name.replace(" ", "_"),
                entity_key=sql_key,
                entity_value=attrs, 
                confidence=100,
                vector=vector
            )
            logger.info(f"⚡ [Immediate Sync] {user_name} :: {sql_key}")

        if self.on_memory_changed:
            self.on_memory_changed(sat_id, user_name)

        if self.router is not None:
            for node_name, attrs in merged.items():
                try:
                    self.router.register_node(bucket_name, node_name, attrs)
                except Exception:
                    pass

    # ----------------------------------------------------------
    # FILE I/O
    # ----------------------------------------------------------
    def _get_existing_buckets(self, user_dir: str) -> list:
        return [
            os.path.basename(f).replace("Bucket_", "").replace(
                ".json", "").replace("_", " ")
            for f in glob.glob(os.path.join(user_dir, "Bucket_*.json"))
        ]

    def _json_path(self, bucket: str, user_dir: str) -> str:
        return os.path.join(user_dir, f"Bucket_{bucket.replace(' ','_')}.json")

    def _load_bucket(self, bucket: str, user_dir: str) -> dict:
        p = self._json_path(bucket, user_dir)
        if os.path.exists(p):
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {"bucket": bucket, "updated": "", "nodes": {}}

    def _save_bucket(self, data: dict, user_dir: str):
        data["updated"] = datetime.now().strftime('%Y-%m-%d')
        json_path = self._json_path(data["bucket"], user_dir)
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        # MD mirror
        lines = [f"# {data['bucket']}", f"_Last updated: {data['updated']}_", ""]
        for node, attrs in data["nodes"].items():
            lines.append(f"- **{node}**")
            if isinstance(attrs, dict):
                for k, v in attrs.items():
                    lines.append(f"  - {k}: {v}")
            else:
                lines.append(f"  - {attrs}")
        md_path = json_path.replace(".json", ".md")
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines))

    def _get_nomic_vector(self, text):
        try:
            payload = {
                "model":  NOMIC_MODEL,
                "prompt": f"search_document: {text}"
            }
            req = urllib.request.Request(
                OLLAMA_EMBED_URL,
                data=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=8.0) as res:
                return json.loads(res.read().decode('utf-8'))['embedding']
        except Exception as e:
            logger.error(f"⚠️ Vector gen failed: {e}")
            return None

    # ----------------------------------------------------------
    # LLM CALL
    # ----------------------------------------------------------
    def _call_llm(self, prompt: str, enforce_json: bool = False):
        payload = {
            "model":   TRIAGE_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream":  False,
            "keep_alive": -1,
            "options": {"temperature": 0.0, "num_predict": 400}
        }
        try:
            res     = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=30.0)
            content = res.json().get("message", {}).get("content", "")
            content = re.sub(r'<think>.*?</think>', '', content,
                             flags=re.DOTALL).strip()
            
            logger.debug(f"🤖 [LLM] Raw Response: {content}")
            if enforce_json:
                # Repair trailing commas before parsing
                matches = re.findall(
                    r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', content, re.DOTALL)
                repaired = []
                for m in matches:
                    try:
                        repaired.append(json.loads(m))
                        repaired[-1] = m  # keep as string for caller
                    except json.JSONDecodeError:
                        fixed = re.sub(r',\s*([}\]])', r'\1', m)
                        repaired.append(fixed)
                return repaired if repaired else []
            return content
        except Exception as e:
            logger.error(f"[LLM] Call failed: {e}")
            return [] if enforce_json else None



# ============================================================
# NIGHTWATCHMAN — JSON → SQL sync after 120s idle
# No re-extraction. JSON files are ground truth.
# Generates Nomic vectors. Handles contradiction via biographer.
# ============================================================
class MemoryWorker:
    def __init__(self, state_manager, on_memory_changed=None):
        self.state   = state_manager
        self.db      = DatabaseManager()
        self.llm     = OllamaBridge()
        self.running = True

        self.IDLE_THRESHOLD = 120

        from services.config import KNOWLEDGE_VAULT_PATH
        self.vault_path = KNOWLEDGE_VAULT_PATH
        os.makedirs(self.vault_path, exist_ok=True)

        self.capture     = RealtimeMemoryCapture(self.db, self.vault_path, on_memory_changed=on_memory_changed)
        self._sync_state = {}   # {json_path: mtime}

    def start(self):
        logger.info(
            f"🦉 NightWatchman Active | "
            f"Idle threshold: {self.IDLE_THRESHOLD}s | "
            f"Vault: {self.vault_path}"
        )
        self.db.reset_processing_tasks()
        threading.Thread(target=self._worker_loop, daemon=True).start()

    # ----------------------------------------------------------
    # IDLE CHECK — reads last_interaction_time (user speech only)
    # ----------------------------------------------------------
    def _is_system_idle(self):
        if self.state.session_mode:
            if any(mode != "IDLE" for mode in self.state.session_mode.values()):
                return False
        last = getattr(self.state, 'last_interaction_time', 0)
        return (time.time() - last) >= self.IDLE_THRESHOLD

    # ----------------------------------------------------------
    # WORKER LOOP
    # ----------------------------------------------------------
    def _worker_loop(self):
        while self.running:
            time.sleep(5)
            if not self._is_system_idle():
                continue

            try:
                with self.db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT task_id, raw_payload FROM memory_queue
                        WHERE status='PENDING'
                        ORDER BY timestamp ASC LIMIT 1
                    """)
                    row = cursor.fetchone()
                    if not row:
                        self._sync_all_vaults()
                        continue
                    task_id, raw_payload = row
                    cursor.execute(
                        "UPDATE memory_queue SET status='PROCESSING' WHERE task_id=?",
                        (task_id,))
                    conn.commit()
            except Exception as e:
                logger.error(f"⚠️ Queue Error: {e}")
                continue

            logger.info(f"🦉 Processing task {task_id}...")
            success = self._process_task(raw_payload, task_id)

            try:
                with self.db.get_connection() as conn:
                    cursor = conn.cursor()
                    if success:
                        cursor.execute(
                            "DELETE FROM memory_queue WHERE task_id=?",
                            (task_id,))
                        logger.info(f"💾 Task {task_id} cleared.")
                    else:
                        cursor.execute(
                            "UPDATE memory_queue SET status='FAILED' WHERE task_id=?",
                            (task_id,))
                    conn.commit()
            except Exception as e:
                logger.error(f"⚠️ Archive failed for task {task_id}: {e}")

    # ----------------------------------------------------------
    # PROCESS TASK — Run LLM Extraction THEN sync to SQL
    # ----------------------------------------------------------
    def _process_task(self, raw_payload: str, task_id: int) -> bool:
        try:
            data         = json.loads(raw_payload)
            sat_id       = data.get("sat_id", 1)
            user_query   = data.get("user_query", "")
            llm_response = data.get("llm_response", "")
            
            user_dir = self.capture._get_user_dir(sat_id)
            
            # 🟢 FIX 1: Run the LLM Extraction Pipeline first! 
            # This generates the perfectly nested JSON and MD files.
            logger.info(f"🧠 Task {task_id}: Extracting JSON nodes via LLM...")
            self.capture._background_triage(sat_id, user_query, llm_response)

            # 🟢 FIX 2: Now that JSON files exist, sync them to the SQL Vector DB.
            synced = self._sync_vault_to_sql(user_dir, user_query)
            logger.info(f"🧠 Task {task_id}: Synced {synced} nodes to SQL.")
            return True
            
        except Exception as e:
            logger.error(f"❌ Task crashed: {e}", exc_info=True)
            return False
        
    def _sync_all_vaults(self):
        for sat_dir in glob.glob(os.path.join(self.vault_path, "sat_*")):
            if os.path.isdir(sat_dir):
                self._sync_vault_to_sql(sat_dir, "")

    def _sync_vault_to_sql(self, user_dir: str, context: str) -> int:
        """
        Reads Bucket_*.json files. For each node:
        - Skip if SQL already has same value
        - Conflict → biographer → update
        - New → Nomic vector → upsert SQL
        """
        synced = 0
        # 🟢 FIX: Extract the active user from the directory path
        user_name = os.path.basename(os.path.normpath(user_dir))

        for json_file in glob.glob(os.path.join(user_dir, "Bucket_*.json")):
            try:
                mtime  = os.path.getmtime(json_file)
                if self._sync_state.get(json_file) == mtime:
                    continue

                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                bucket = data.get("bucket", "Unknown")
                for node_name, attrs in data.get("nodes", {}).items():
                    if node_name.strip().lower() in NODE_STOPWORDS:
                        continue

                    sql_key = (
                        f"{bucket.lower()}."
                        f"{node_name.lower().replace(' ', '_')}"
                    )
                    
                    # OPTIMIZATION: Keep as a dictionary, or fallback to string.
                    # Convert to strict JSON for the comparison check.
                    if isinstance(attrs, dict):
                        compare_value = json.dumps(attrs, sort_keys=True, ensure_ascii=False)
                        db_payload = attrs # Pass the raw dict to the optimized DB
                    else:
                        compare_value = str(attrs)
                        db_payload = str(attrs)

                    if not compare_value.strip() or compare_value == "{}":
                        continue

                    # 🟢 FIX: Pass the user_name to check against their specific partition
                    existing = self._get_sql_fact(user_name, sql_key)
                    
                    # Normalize existing DB string for comparison if it's JSON
                    existing_compare = existing
                    if existing and existing.startswith("{"):
                        try:
                            existing_dict = json.loads(existing)
                            existing_compare = json.dumps(existing_dict, sort_keys=True, ensure_ascii=False)
                        except:
                            pass

                    if existing_compare and existing_compare == compare_value:
                        continue

                    if not self._is_system_idle():
                        logger.info("🛑 User active — pausing sync.")
                        return synced

                    if existing_compare and existing_compare != compare_value:
                        narrative = self._biographer_resolve(
                            sql_key, existing, compare_value, context)
                        if narrative == "ABORTED":
                            return synced
                        self._write_narrative(bucket, sql_key, narrative, user_dir)

                    # Create vector based on a readable string, not raw JSON brackets
                    flat_text_for_vector = ", ".join(f"{k}: {v}" for k, v in attrs.items()) if isinstance(attrs, dict) else str(attrs)
                    vector = self._get_nomic_vector(f"{node_name}: {flat_text_for_vector}")
                    
                    # 🟢 FIX: Send the user_id to the database save function
                    self.db.save_core_fact(
                        user_id=user_name,
                        bucket=bucket.replace(" ", "_"),
                        entity_key=sql_key,
                        entity_value=db_payload, 
                        confidence=100,
                        vector=vector
                    )
                    synced += 1

                self._sync_state[json_file] = mtime

            except Exception as e:
                logger.error(f"⚠️ Sync failed for {json_file}: {e}")
        return synced

    # ----------------------------------------------------------
    # HELPERS
    # ----------------------------------------------------------
    # 🟢 FIX: Update helper signature and SQL query to enforce user isolation
    def _get_sql_fact(self, user_id: str, key: str):
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT entity_value FROM core_memory WHERE user_id=? AND entity_key=?",
                    (user_id, key.lower(),))
                row = cursor.fetchone()
                return row[0] if row else None
        except Exception:
            return None
    # ----------------------------------------------------------
    # BIOGRAPHER — contradiction resolution only
    # ----------------------------------------------------------
    def _biographer_resolve(self, key, old, new, context) -> str:
        if not self._is_system_idle():
            return "ABORTED"
        prompt = (
            f"Update this memory in 1-2 sentences. Third person. No markdown.\n\n"
            f"FACT: {key.replace('.', ' ')}\n"
            f"WAS: {old}\nNOW: {new}\nCONTEXT: {context[:100]}"
        )
        try:
            payload = {
                "model":   TRIAGE_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream":  False,
                "keep_alive": -1,
                "options": {"temperature": 0.0, "num_predict": 100}
            }
            res     = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=20.0)
            content = res.json().get("message", {}).get("content", "").strip()
            content = re.sub(r'<think>.*?</think>', '', content,
                             flags=re.DOTALL).strip()
            return content or f"Updated: {key} changed to {new}."
        except Exception as e:
            logger.error(f"⚠️ Biographer failed: {e}")
            return f"Updated: {key} changed to {new}."

    def _write_narrative(self, bucket, key, narrative, user_dir):
        safe     = bucket.replace(" ", "_").title()
        filepath = os.path.join(user_dir, f"Bucket_{safe}.md")
        header   = f"### {key.replace('.', ' ').title()}"
        entry    = (f"{header}\n{narrative}\n"
                    f"*Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n")
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            if header in content:
                pattern = re.compile(
                    rf"{re.escape(header)}\n.*?(?=\n### |\Z)", re.DOTALL)
                new_content = pattern.sub(entry, content)
            else:
                new_content = content.rstrip() + f"\n\n{entry}"
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(new_content)
        else:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(f"# Memory: {safe}\n\n{entry}")

