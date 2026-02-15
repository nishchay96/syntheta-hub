import subprocess
import logging
import sys
import os
import json

# 🔧 IMPORT DATA MODELS
sys.path.append(os.path.join(os.path.dirname(__file__), '../../'))
from core.data_models import GoldenPacket

logger = logging.getLogger("LLM")

# ⚙️ CONFIGURATION
# Matches the model name you successfully ran in terminal
MODEL_NAME = "llama3.2" 

class OllamaBridge:
    def __init__(self):
        logger.info(f"LLM Bridge Init | Unified Core: {MODEL_NAME} (CLI Mode)")

    def _call_ollama_cli(self, prompt, system_prompt):
        """
        Uses the native Linux 'ollama run' command via subprocess.
        This bypasses HTTP networking issues.
        """
        try:
            # Construct a single prompt block because CLI arguments are simple strings
            # We force the System Prompt structure manually
            full_prompt = (
                f"{system_prompt}\n\n"
                f"User: {prompt}\n"
                f"Assistant:"
            )
            
            # Run the command: ollama run <model> <prompt>
            # capture_output=True grabs stdout (the answer)
            result = subprocess.run(
                ["ollama", "run", MODEL_NAME, full_prompt],
                capture_output=True,
                text=True,
                encoding='utf-8',
                check=True  # Raises CalledProcessError if return code != 0
            )
            
            # Clean up the output
            response = result.stdout.strip()
            return response

        except subprocess.CalledProcessError as e:
            logger.error(f"Ollama CLI Error (Exit Code {e.returncode}): {e.stderr}")
            return None
        except FileNotFoundError:
            logger.error("❌ CRITICAL: 'ollama' command not found. Is it installed and in PATH?")
            return None
        except Exception as e:
            logger.error(f"Unknown Subprocess Error: {e}")
            return None

    def generate_slm_prompt(self, packet: GoldenPacket) -> str:
        """
        Phase 4: The Wrapper.
        Unzips the Golden Packet into a System Instruction.
        """
        # Safely get fields with defaults
        role = packet.get('role', 'You are Syntheta.')
        ctx = packet.get('ctx', 'general')
        emotion = packet.get('emotion', 'neutral')
        entities = packet.get('entities', {})
        history = packet.get('history', '')

        return (
            f"{role}\n"
            f"--- CONTEXT ---\n"
            f"TOPIC: {ctx}\n"
            f"USER EMOTION: {emotion}\n"
            f"KNOWN ENTITIES: {entities}\n"
            f"--- MEMORY ---\n"
            f"{history}\n"
            "----------------\n"
            "INSTRUCTION: Answer briefly (under 2 sentences). Be helpful and friendly."
        )

    def generate(self, packet: GoldenPacket):
        """
        Unified Processing: Logic + Personality in one shot.
        Replaces old 'think' method.
        """
        # 1. Build the Dynamic System Prompt
        system_instruction = self.generate_slm_prompt(packet)
        
        # 2. Extract User Input
        user_text = packet.get('input', '')

        logger.info(f"[LLM] Processing Packet: '{user_text}' | Context: {packet.get('ctx', 'unknown')}")
        
        # 3. Call Ollama via CLI
        response = self._call_ollama_cli(user_text, system_instruction)
        
        if not response:
            return "I'm having trouble connecting to my brain."
        return response

    # =========================================================
    # 🔧 COMPATIBILITY LAYER (Fixes PiManager Crash)
    # =========================================================
    def think(self, user_text, context=[]):
        """
        Legacy Adapter for PiManager.
        Wraps old-style arguments into a temporary Golden Packet.
        """
        # Convert list context to string
        history_str = ""
        if isinstance(context, list):
            for item in context[-3:]: # Take last 3
                if isinstance(item, dict):
                    # Try to extract content safely
                    role = item.get('role', 'user')
                    content = item.get('content', '') or item.get('text', '')
                    history_str += f"{role}: {content}\n"
                else:
                    history_str += str(item) + "\n"
        
        # Create a dummy packet
        packet: GoldenPacket = {
            "role": "You are Syntheta.",
            "ctx": "reflex_fallback",
            "history": history_str,
            "entities": {},
            "emotion": "neutral",
            "input": user_text
        }
        
        return self.generate(packet)

    def speak(self, raw_thought, tone="neutral"):
        """Passthrough (Already styled by think)"""
        return raw_thought