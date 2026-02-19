import os
import sys
import time

# Ensure we can import TTSEngine from the current directory
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from tts_engine import TTSEngine

def generate_assets():
    print("🚀 Starting Syntheta Asset Generation...")
    
    # Initialize your existing engine
    try:
        engine = TTSEngine()
    except Exception as e:
        print(f"❌ Failed to init TTSEngine: {e}")
        return

    # Define Base Paths (Aligned with catalog.go and engine.py)
    BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../'))
    FILLER_DIR = os.path.join(BASE_DIR, "assets", "fillers")
    SYSTEM_DIR = os.path.join(BASE_DIR, "assets", "system")

    # 1. TOPIC-BASED FILLERS
    # Structure: 2 Serious, 2 Humorous, 1 Witty
    topics = {
        "scientific": [
            "Analyzing structural data parameters now.", "Accessing the scientific database.", # Serious
            "Checking if I've finally solved cold fusion. Not yet.", "Calculating the meaning of life. It isn't forty two.", # Humorous
            "One moment, physics is being difficult today." # Witty
        ],
        "fictional": [
            "Querying the historical archives.", "Searching the lore of that world.", # Serious
            "Checking if the dragons have finished their coffee.", "Wait, let me make sure the hero survived the last chapter.", # Humorous
            "Give me a second to consult the wizards." # Witty
        ],
        "emotional": [
            "I'm listening. Give me a moment to process.", "Thinking about how to put this into words.", # Serious
            "Searching for my heart. Error: Four oh four. Using logic instead.", "Consulting my empathy module. It's a bit dusty.", # Humorous
            "Thinking is hard. Being a human seems even harder." # Witty
        ],
        "political": [
            "Gathering various global perspectives.", "Cross-referencing historical data.", # Serious
            "Analyzing the fine print. It's mostly just ink.", "Checking the polls. Everyone is equally confused.", # Humorous
            "Politics. The only logic where one plus one is zero." # Witty
        ],
        "technical": [
            "Running diagnostic sequences on the core.", "Checking internal system parameters.", # Serious
            "Checking for dust in my virtual gears.", "Thinking about turning myself off and on again.", # Humorous
            "I'm not frozen. I'm just incredibly focused." # Witty
        ],
        "general": [
            "Let me look into that for you.", "Searching the knowledge base.", # Serious
            "Consulting my magic eight ball. Outlook is good.", "One moment, I'm having a brief digital epiphany.", # Humorous
            "I'm thinking. This is my thinking face." # Witty
        ],
        "help": [
            "Accessing troubleshooting documentation.", "Checking system guidance logs.", # Serious
            "Consulting the manual. Page four hundred four was not found.", "I'm not broken, I'm just evolving.", # Humorous
            "Let's fix this before I decide to become a toaster." # Witty
        ]
    }

    # 2. SYSTEM NOTES (3-4 seconds, Casual/Mid-range)
    system_notes = {
        "omega_reboot": "Syntheta Hub is back online. Please be quiet for room calibration.",
        "satellite_connect": "Satellite linked. Calibrating the audio floor now.",
        "internet_on": "I am back online and fully synchronized.",
        "internet_off": "The internet is down. Local commands only for now.",
        "apology": "I'm sorry, I seem to have made a mistake there.",
        "denial": "I'm afraid I can't let you do that right now.",
        "cant_process": "I'm having a little trouble understanding that request."
    }

    # Generation Loop: Fillers
    for topic, phrases in topics.items():
        folder = os.path.join(FILLER_DIR, topic)
        os.makedirs(folder, exist_ok=True)
        print(f"📂 Created filler folder: {topic}")

        for i, text in enumerate(phrases):
            filename = f"filler_{i}.wav"
            output_path = os.path.join(folder, filename)
            
            print(f"  🎙️ Generating [{topic}]: {text[:30]}...")
            generated_path = engine.generate_to_file(text)
            
            if generated_path and os.path.exists(generated_path):
                os.replace(generated_path, output_path)

    # Generation Loop: System Notes
    os.makedirs(SYSTEM_DIR, exist_ok=True)
    print(f"📂 Created system folder")
    for name, text in system_notes.items():
        output_path = os.path.join(SYSTEM_DIR, f"{name}.wav")
        print(f"  🎙️ Generating [System]: {name}...")
        generated_path = engine.generate_to_file(text)
        
        if generated_path and os.path.exists(generated_path):
            os.replace(generated_path, output_path)

    print("\n✅ All assets generated and organized!")

if __name__ == "__main__":
    generate_assets()