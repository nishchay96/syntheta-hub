from typing import TypedDict, Dict, List, Optional, Any

# ==========================================
# 📜 THE GOLDEN PACKET (Input Schema)
# ==========================================
class GoldenPacket(TypedDict):
    """
    The Single Source of Truth for every interaction.
    Built by StateManager, consumed by LLM Bridge.
    """
    # 1. THE INSTRUCTION (Identity)
    role: str           
    
    # 2. THE SITUATION (Routing)
    ctx: str            
    
    # 3. THE MEMORY (Context Window)
    history: str        
    
    # 4. THE BRIEFCASE (Extracted Knowledge)
    entities: Dict[str, Any] 
    
    # 5. THE VIBE (Acoustic/Sentiment Analysis)
    emotion: str        
    
    # 6. THE TRIGGER (The Text)
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
    entities: Dict[str, Any]
    # Structure: [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
    history_buffer: List[Dict[str, str]] 
    last_interaction: float 
    is_active: bool