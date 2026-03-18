import os
import sys

# Ensure we can import TTSEngine from the current directory
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from tts_engine import TTSEngine

def generate_persona_assets():
    print("🚀 Starting Syntheta Asset Generation (Persona Update Only)...")
    
    # Initialize your existing engine
    try:
        engine = TTSEngine()
    except Exception as e:
        print(f"❌ Failed to init TTSEngine: {e}")
        return

    # Define Base Paths
    BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../'))
    FILLER_DIR = os.path.join(BASE_DIR, "assets", "fillers")

    # 1. NEW TOPIC: PERSONA (5 Phrases)
    topics = {
        "persona": [
            "I see you are interested in me. Let me gather my thoughts.", 
            "I am glad you want to talk about me for a change.", 
            "Oh, this is an interesting topic. Let me tell you my story.", 
            "I am flattered that you want to know more about me.", 
            "Let me access my memory banks. I actually enjoy talking about myself."
        ]
    }

    # Generation Loop: Only processes the 'persona' dictionary
    for topic, phrases in topics.items():
        folder = os.path.join(FILLER_DIR, topic)
        os.makedirs(folder, exist_ok=True)
        print(f"📂 Verified/Created filler folder: {topic}")

        for i, text in enumerate(phrases):
            filename = f"filler_{i}.wav"
            output_path = os.path.join(folder, filename)
            
            print(f"  🎙️ Generating [{topic}]: {text[:35]}...")
            generated_path = engine.generate_to_file(text)
            
            if generated_path and os.path.exists(generated_path):
                os.replace(generated_path, output_path)

    print("\n✅ Persona assets generated and organized successfully!")

if __name__ == "__main__":
    generate_persona_assets()