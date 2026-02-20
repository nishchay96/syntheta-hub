from typing import TypedDict, Dict, List, Optional, Any

# ==========================================
# 📜 THE GOLDEN PACKET (Input Schema)
# ==========================================
class GoldenPacket(TypedDict):
    """
    The Single Source of Truth for every interaction.
    Built by StateManager, consumed by LLM Bridge.
    """
    role: str           
    ctx: str            
    history: str        
    entities: Dict[str, Any] 
    emotion: str        
    input: str          

# ==========================================
# 🧠 COGNITIVE STATE (Memory Storage)
# ==========================================
class CognitiveState(TypedDict):
    """
    The Long-Term Memory storage structure.
    Persists data across turns for each Satellite.
    """
    topic: str
    active_subject: str  # 🟢 NEW: Highly specific dynamic subject from the SLM
    entities: Dict[str, Any]
    history_buffer: List[Dict[str, str]] 
    last_interaction: float 
    is_active: bool