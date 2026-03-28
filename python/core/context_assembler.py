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

    def _user_dir(self, user_id):
        if not user_id:
            return self.vault_path
        return os.path.join(self.vault_path, str(user_id).lower())

    def _format_node_context(self, bucket, node_name, attrs):
        if isinstance(attrs, dict):
            detail = ", ".join(f"{k}: {v}" for k, v in attrs.items())
        else:
            detail = str(attrs)
        return f"[{bucket}] {node_name}: {detail}"

    def _get_exact_node_context(self, user_id, matched_memory_node):
        if not user_id or not matched_memory_node or "::" not in matched_memory_node:
            return ""
        bucket, node_name = matched_memory_node.split("::", 1)
        json_path = os.path.join(self._user_dir(user_id), f"Bucket_{bucket.replace(' ', '_')}.json")
        if not os.path.exists(json_path):
            return ""
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            nodes = data.get("nodes", {})
            if node_name in nodes:
                return self._format_node_context(bucket, node_name, nodes[node_name])
            target = node_name.lower().strip()
            for existing_name, attrs in nodes.items():
                if str(existing_name).lower().strip() == target:
                    return self._format_node_context(bucket, existing_name, attrs)
        except Exception as e:
            logger.warning(f"Failed to read exact node from {json_path}: {e}")
        return ""

    # =========================================================
    # 🟢 PRIMARY: JSON node-level retrieval (real-time facts)
    # Reads Bucket_*.json files written by RealtimeMemoryCapture
    # Returns only the specific nodes relevant to the query
    # — not entire bucket dumps
    # =========================================================
    def _search_knowledge_json(self, query, top_k=2, user_id=None):
        """
        Node-level semantic retrieval from JSON bucket files.
        Scores each node individually against query words.
        Much more precise than reading full markdown files.
        Covers facts captured in the current session immediately
        without waiting for NightWatchman to run.
        """
        user_dir = self._user_dir(user_id)
        if not os.path.exists(user_dir):
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

        pattern = os.path.join(user_dir, "Bucket_*.json")
        for json_file in glob.glob(pattern):
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
                        scored_nodes.append((score, self._format_node_context(bucket, node_name, attrs)))
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
    def _search_knowledge_graph(self, query, target_buckets=None, top_k=2, user_id=None):
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

        target_buckets = set(target_buckets or [])
        snippets = []

        profile_dir = self._user_dir(user_id) if user_id else None
        for bucket in target_buckets:
            filepath = os.path.join(profile_dir, f"Bucket_{bucket}.md") if profile_dir else ""
            if os.path.exists(filepath):
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                    snippets.append((100, content.strip()[:1000], f"Bucket_{bucket}"))
                except Exception as e:
                    logger.error(f"Failed to read {bucket}: {e}")

        # Fallback: full-text scan across all .md files
        if not snippets:
            scan_root = profile_dir if profile_dir and os.path.exists(profile_dir) else self.vault_path
            for root, _, files in os.walk(scan_root):
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
    def build_context_string(self, user_id, user_input="", recent_queries=None, matched_memory_node=None):
        """
        Assembles context block injected into GoldenPacket.
        Three layers:
          1. System clock
          2. Recent session timeline
          3. Targeted memory retrieval — exact matched node first, then JSON/MD search
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

        # 3. Memory retrieval — exact matched node, then JSON nodes, then MD fallback
        if user_input:
            knowledge_context = self._get_exact_node_context(user_id, matched_memory_node)
            if not knowledge_context:
                knowledge_context = self._search_knowledge_json(user_input, top_k=2, user_id=user_id)

            # Fallback: markdown narrative (covers past sessions via NightWatchman)
            if not knowledge_context:
                target_buckets = set()
                if matched_memory_node and "::" in matched_memory_node:
                    target_buckets.add(matched_memory_node.split("::", 1)[0].replace(" ", "_"))
                knowledge_context = self._search_knowledge_graph(
                    user_input, target_buckets=target_buckets, top_k=2, user_id=user_id)

            if knowledge_context:
                context_blocks.append(
                    f"--- MEMORY ---\n{knowledge_context}"
                )

        if not context_blocks:
            return ""
        return "\n\n".join(context_blocks) + "\n--------------------------------\n"
