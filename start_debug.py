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
    print("🚀 SYNTHETA SIMPLE LAUNCHER (DEBUG MODE)")
    print("========================================")

    # 1. FIND PYTHON
    venv_python = os.path.join(PY_DIR, "venv", "bin", "python")
    if os.path.exists(venv_python):
        py_exe = venv_python
        print(f"✅ Using Venv Python: {py_exe}")
    else:
        py_exe = sys.executable
        print(f"⚠️ Using System Python: {py_exe}")

    # 2. COMPILE GO
    print("\n🔨 Compiling Go Hub...")
    try:
        subprocess.run(["go", "build", "-o", "syntheta-hub", "./cmd"], cwd=GO_DIR, check=True)
        print("✅ Compilation Successful.")
    except Exception as e:
        print(f"❌ Go Compile Failed: {e}")
        sys.exit(1)

    # 3. LAUNCH GO HUB (With 'Keep Alive')
    print("\n📡 Launching Go Bridge...")
    go_bin = os.path.join(GO_DIR, "syntheta-hub")
    
    term = shutil.which("gnome-terminal")
    if term:
        # 🔧 THE FIX: Wrap command in bash to pause on exit
        # command: bash -c "/path/to/hub; echo 'Press Enter...'; read line"
        cmd_str = f'"{go_bin}"; echo "\n[PROCESS EXITED]"; echo "Press Enter to close window..."; read line'
        
        subprocess.Popen([
            term, 
            "--", 
            "bash", 
            "-c", 
            cmd_str
        ], cwd=GO_DIR)
        
        print("✅ Go Hub running in new window (Will stay open on crash).")
    else:
        print("⚠️ No gnome-terminal found. Running inside this window (Background).")
        subprocess.Popen([go_bin], cwd=GO_DIR)

    time.sleep(2)

    # 4. LAUNCH PYTHON BRAIN
    print("\n🧠 Launching Python Brain...")
    print("--------------------------------")
    try:
        subprocess.run([py_exe, "-u", "main.py"], cwd=PY_DIR)
    except KeyboardInterrupt:
        print("\n🛑 Stopped by user.")

if __name__ == "__main__":
    main()