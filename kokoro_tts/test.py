from kokoro import KPipeline
import soundfile as sf
import time
import numpy as np
import torch

# ==========================================
# ⚙️ HARDWARE CONFIG: CPU (STABLE)
# ==========================================
DEVICE = 'cpu'

print(f"🚀 Initializing Kokoro-82M...")

try:
    # 1. Initialize Pipeline (British English)
    # repo_id argument suppresses the warning
    pipeline = KPipeline(lang_code='b', device=DEVICE, repo_id='hexgrad/Kokoro-82M')

    # 2. 🟢 LOAD VALID VOICES
    # We mix 'bf_emma' (British, Warm) with 'af_bella' (American, High Energy)
    # Kokoro allows cross-accent mixing! This gives you Bella's energy with Emma's accent.
    print("⬇️ Loading Voice: bf_emma (British)...")
    voice_british = pipeline.load_voice('bf_emma')
    
    print("⬇️ Loading Voice: af_bella (Energy)...")
    voice_energy = pipeline.load_voice('af_bella')
    
    # 3. 🟢 CUSTOM BLEND (60% British / 40% Energy)
    # Adjusting the ratio ensures the British accent stays dominant
    syntheta_voice = (voice_british * 0.6) + (voice_energy * 0.4)

    welcome_text = (
        "Hello Nishchay! Syntheta is officially online. "
        "I've completed my system diagnostics and I'm feeling absolutely brilliant today! "
        "Ready to assist you with whatever you need."
    )

    print(f"🎙️ Generating for Syntheta (Persona: Emma-Bella Hybrid)...")

    start_time = time.time()
    
    # Speed 1.1 helps the "Mid-20s" perception
    generator = pipeline(
        welcome_text, 
        voice=syntheta_voice, 
        speed=1.1, 
        split_pattern=r'\n+'
    )
    
    full_audio = []
    for i, (gs, ps, audio) in enumerate(generator):
        full_audio.append(audio)
    
    audio_data = np.concatenate(full_audio)
    latency = time.time() - start_time

    # Performance Stats
    sr = 24000 
    duration = len(audio_data) / sr
    rtf = latency / duration
    
    print(f"\n--- 📊 PERFORMANCE STATS ---")
    print(f"⏱️ Latency: {latency:.2f}s")
    print(f"⚡ RTF: {rtf:.4f}")
    print(f"----------------------------\n")

    sf.write("syntheta_welcome_british.wav", audio_data, sr)
    print(f"💾 Saved to syntheta_welcome_british.wav")

except Exception as e:
    print(f"\n❌ Error: {e}")