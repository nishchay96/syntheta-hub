#!/usr/bin/env python3
import os
import sys
import subprocess
import time
import shutil

# ==================== CONFIG ====================
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
GO_DIR = os.path.join(ROOT_DIR, "go")
PY_DIR = os.path.join(ROOT_DIR, "python")

def main():
    print("🚀 SYNTHETA SIMPLE LAUNCHER")
    print("===========================")

    # 1. FIND PYTHON (Prioritize Venv)
    # We look for the 'venv' you created earlier
    venv_python = os.path.join(PY_DIR, "venv", "bin", "python")
    if os.path.exists(venv_python):
        py_exe = venv_python
        print(f"✅ Found Virtual Env: {py_exe}")
    else:
        py_exe = sys.executable
        print(f"⚠️ Venv not found, using System Python: {py_exe}")

    # 2. COMPILE GO
    print("\n🔨 Compiling Go Hub...")
    try:
        subprocess.run(["go", "build", "-o", "syntheta-hub", "./cmd"], cwd=GO_DIR, check=True)
        print("✅ Compilation Successful.")
    except Exception as e:
        print(f"❌ Go Compile Failed: {e}")
        sys.exit(1)

    # 3. LAUNCH GO HUB (New Window)
    print("\n📡 Launching Go Bridge...")
    go_bin = os.path.join(GO_DIR, "syntheta-hub")
    
    # Try to find a terminal emulator to pop a new window
    term = shutil.which("gnome-terminal")
    if term:
        # Launch in new window so you can see logs
        subprocess.Popen([term, "--", go_bin], cwd=GO_DIR)
        print("✅ Go Hub running in new window.")
    else:
        # Fallback: Run in background if no terminal found
        subprocess.Popen([go_bin], cwd=GO_DIR)
        print("⚠️ No gnome-terminal found. Go Hub running in background.")

    # Give Go a second to bind ports
    time.sleep(2)

    # 4. LAUNCH PYTHON BRAIN (Current Window)
    print("\n🧠 Launching Python Brain...")
    print("--------------------------------")
    try:
        # -u = Unbuffered output (instant logs)
        subprocess.run([py_exe, "-u", "main.py"], cwd=PY_DIR)
    except KeyboardInterrupt:
        print("\n🛑 Stopped by user.")

if __name__ == "__main__":
    main()