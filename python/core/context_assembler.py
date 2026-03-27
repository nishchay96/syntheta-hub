import os
import json
import glob
import logging
import re
from datetime import datetime

logger = logging.getLogger("ContextAssembler")


class ContextAssembler:
    def __init__(self, db_manager, vault_path=None):
        self.db = db_manager
        from services.config import KNOWLEDGE_VAULT_PATH
        self.vault_path = vault_path or KNOWLEDGE_VAULT_PATH

    # =========================================================
    # 🟢 PRIMARY: JSON node-level retrieval (real-time facts)
    # Reads Bucket_*.json files written by RealtimeMemoryCapture
    # Returns only the specific nodes relevant to the query
    # — not entire bucket dumps
    # =========================================================
    def _search_knowledge_json(self, query, top_k=2):
        """
        Node-level semantic retrieval from JSON bucket files.
        Scores each node individually against query words.
        Much more precise than reading full markdown files.
        Covers facts captured in the current session immediately
        without waiting for NightWatchman to run.
        """
        if not os.path.exists(self.vault_path):
            return ""

        stop_words = {
            "the", "is", "at", "which", "on", "in", "a", "an", "and",
            "or", "what", "who", "where", "how", "tell", "me", "update",
            "information", "do", "have", "was", "has", "been"
        }
        query_words = [
            w for w in re.findall(r'\w+', query.lower())
            if w not in stop_words and len(w) > 2
        ]
        if not query_words:
            return ""

        scored_nodes = []

        for json_file in glob.glob(
                os.path.join(self.vault_path, "**", "Bucket_*.json"),
                recursive=True):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                bucket = data.get("bucket", "Unknown")

                for node_name, attrs in data.get("nodes", {}).items():
                    node_text = node_name.lower()
                    if isinstance(attrs, dict):
                        attr_text = " ".join(
                            f"{k} {v}" for k, v in attrs.items()
                        ).lower()
                    else:
                        attr_text = str(attrs).lower()

                    # Node name match weighted higher than attribute match
                    score = sum(
                        2 if w in node_text else (1 if w in attr_text else 0)
                        for w in query_words
                    )
                    if score > 0:
                        if isinstance(attrs, dict):
                            detail = ", ".join(
                                f"{k}: {v}" for k, v in attrs.items()
                            )
                        else:
                            detail = str(attrs)
                        scored_nodes.append(
                            (score, f"[{bucket}] {node_name}: {detail}")
                        )
            except Exception as e:
                logger.warning(f"Failed to read {json_file}: {e}")

        scored_nodes.sort(key=lambda x: x[0], reverse=True)
        top = [text for _, text in scored_nodes[:top_k]]
        return "\n".join(top) if top else ""

    # =========================================================
    # 🟡 SECONDARY: Markdown narrative search (deep history)
    # Reads Bucket_*.md files written by NightWatchman
    # Used as fallback when JSON nodes return nothing
    # Contains richer narrative context from past sessions
    # =========================================================
    def _search_knowledge_graph(self, query, core_facts, top_k=2):
        """
        Bucket-level keyword retrieval from markdown narrative files.
        Falls back to this when JSON node search returns empty.
        Also uses SQL core_memory keys for direct bucket routing.
        """
        if not os.path.exists(self.vault_path):
            return ""

        stop_words = {
            "the", "is", "at", "which", "on", "in", "a", "an", "and",
            "or", "what", "who", "where", "how", "tell", "me", "what's",
            "update", "information", "my", "do", "i", "have"
        }
        query_words = [
            w for w in re.findall(r'\w+', query.lower())
            if w not in stop_words
        ]
        if not query_words:
            return ""

        target_buckets = set()
        snippets = []

        # Direct bucket routing via SQL fact keys
        for key, data in core_facts.items():
            key_words = key.split('_')
            if any(w in query_words for w in key_words):
                target_buckets.add(
                    data.get("bucket", "General").title().replace(" ", "_")
                )

        for bucket in target_buckets:
            filepath = os.path.join(self.vault_path, f"Bucket_{bucket}.md")
            if os.path.exists(filepath):
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                    snippets.append((100, content.strip()[:1000], f"Bucket_{bucket}"))
                except Exception as e:
                    logger.error(f"Failed to read {bucket}: {e}")

        # Fallback: full-text scan across all .md files
        if not snippets:
            for root, _, files in os.walk(self.vault_path):
                for file in files:
                    if not file.endswith(".md"):
                        continue
                    file_lower = file.lower()
                    try:
                        with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                            content = f.read()
                        content_lower = content.lower()
                        score = sum(3 for w in query_words if w in file_lower)
                        score += sum(1 for w in query_words if w in content_lower)
                        if score >= 3:
                            snippets.append(
                                (score, content.strip()[:800],
                                 file.replace('.md', ''))
                            )
                    except Exception:
                        continue

        snippets.sort(key=lambda x: x[0], reverse=True)
        seen = set()
        top_snippets = []
        for score, text, source in snippets:
            if source not in seen:
                top_snippets.append((score, text, source))
                seen.add(source)
            if len(top_snippets) >= top_k:
                break

        if not top_snippets:
            return ""
        return "\n\n".join(
            [f"[{src}]:\n{txt}..." for _, txt, src in top_snippets]
        )

    # =========================================================
    # MAIN ENTRY — called by engine._handle_normal_command()
    # =========================================================
    def build_context_string(self, user_id, user_input="", recent_queries=None):
        """
        Assembles context block injected into GoldenPacket.
        Three layers:
          1. System clock
          2. User profile from SQL core_memory
          3. Memory retrieval — JSON nodes first, MD narrative fallback
        """
        context_blocks = []
        now = datetime.now()

        # 1. System clock — always present
        context_blocks.append(
            f"--- SYSTEM CLOCK ---\n"
            f"- Time: {now.strftime('%I:%M %p')}\n"
            f"- Date: {now.strftime('%A, %B %d, %Y')}"
        )

        # 2. Recent conversation timeline from the active session when available.
        # Falling back to the global ledger here pollutes the prompt with older runs.
        if recent_queries:
            context_blocks.append(
                "--- RECENT TIMELINE ---\n"
                + "\n".join([f"- {e}" for e in recent_queries[-5:]])
            )

        # 3. User profile from SQL core_memory (NightWatchman writes this)
        core_facts = self.db.get_all_core_facts(user_id)
        if core_facts:
            profile_lines = []
            for k, data in core_facts.items():
                val    = data.get("value", "")
                bucket = data.get("bucket", "General")
                profile_lines.append(f"- {k.upper()}: {val} [Bucket: {bucket}]")
            context_blocks.append(
                "--- USER PROFILE ---\n" + "\n".join(profile_lines)
            )

        # 4. Memory retrieval — JSON nodes (real-time) + MD narrative (deep)
        if user_input:
            # Primary: JSON node-level (covers current session immediately)
            knowledge_context = self._search_knowledge_json(user_input, top_k=2)

            # Fallback: markdown narrative (covers past sessions via NightWatchman)
            if not knowledge_context:
                knowledge_context = self._search_knowledge_graph(
                    user_input, core_facts, top_k=2)

            if knowledge_context:
                context_blocks.append(
                    f"--- MEMORY ---\n{knowledge_context}"
                )

        if not context_blocks:
            return ""
        return "\n\n".join(context_blocks) + "\n--------------------------------\n"
