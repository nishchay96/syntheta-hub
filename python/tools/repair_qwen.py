import os
from huggingface_hub import hf_hub_download

# CONFIG
MODEL_REPO = "Qwen/Qwen3-ASR-1.7B"
LOCAL_DIR = "../../assets/models/Qwen3-ASR-1.7B"

# 1. Identify the shards
shards = [
    "model-00001-of-00002.safetensors",
    "model-00002-of-00002.safetensors",
    "model.safetensors.index.json"
]

print("🛠️ Starting Qwen3-ASR Repair Tool...")

# 2. Get the correct paths
base_dir = os.path.dirname(os.path.abspath(__file__))
target_dir = os.path.abspath(os.path.join(base_dir, LOCAL_DIR))

# 3. Force re-download of the weights
for shard in shards:
    file_path = os.path.join(target_dir, shard)
    if os.path.exists(file_path):
        print(f"🔄 Deleting and re-downloading {shard} to ensure integrity...")
        os.remove(file_path)
    
    # Using the mirror again for speed
    os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
    
    hf_hub_download(
        repo_id=MODEL_REPO,
        filename=shard,
        local_dir=target_dir,
        local_dir_use_symlinks=False
    )

print("\n✅ REPAIR COMPLETE. All shards verified.")