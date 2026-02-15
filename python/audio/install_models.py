import os
import ssl
import certifi
from faster_whisper import download_model

# 1. FIX SSL ISSUES (Bypass verify if needed)
os.environ['CURL_CA_BUNDLE'] = ''
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

print("--- DOWNLOADING SYNTHETA MODELS ---")
print("This may take a minute. Please wait...")

try:
    # 2. Download 'base.en' (The one crashing your system)
    print("\n1. Downloading Whisper 'base.en'...")
    model_path = download_model("base.en")
    print(f"✅ Success! Saved to: {model_path}")

    # 3. Download 'tiny' (Just in case)
    print("\n2. Downloading Whisper 'tiny'...")
    model_path = download_model("tiny")
    print(f"✅ Success! Saved to: {model_path}")

except Exception as e:
    print(f"\n❌ ERROR: {e}")
    print("Tip: If this fails, check your firewall or VPN.")

print("\n--- DONE ---")