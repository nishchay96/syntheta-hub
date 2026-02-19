#!/usr/bin/env python3
import os
import sys
import subprocess
import time
import shutil
import signal
import glob
import requests

# ==============================================================================
# ⚙️ CONFIGURATION
# ==============================================================================
# Robust Root Path Detection
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

# Venv Paths
VENV_PATH = os.path.join(ROOT_DIR, "venv")
VENV_PYTHON = os.path.join(VENV_PATH, "bin", "python")
VENV_AUDIO_PATH = os.path.join(ROOT_DIR, "venv-audio")

# Service Directories
GO_DIR = os.path.join(ROOT_DIR, "go")
PY_DIR = os.path.join(ROOT_DIR, "python")

# The Surgical List
TARGET_PORTS = [5555, 5556, 6000, 6002, 9001]
OLLAMA_API_URL = "http://localhost:11434/api/tags"

def log(msg, level="INFO"):
    prefix = {"INFO": "ℹ️", "WARN": "⚠️", "ERROR": "❌", "BOOT": "🚀", "CHECK": "🔍"}.get(level, "")
    print(f"[{level}] {prefix} {msg}", flush=True)

# ==============================================================================
# 🔍 ENVIRONMENT UTILS
# ==============================================================================
def get_site_packages(venv_base):
    for lib_dirname in ["lib", "lib64"]:
        lib_path = os.path.join(venv_base, lib_dirname)
        if not os.path.exists(lib_path): continue
        paths = glob.glob(os.path.join(lib_path, "python3*", "site-packages"))
        if paths: return paths[0]
    return None

def get_sanitized_env():
    env = os.environ.copy()
    keys_to_strip = ["LD_LIBRARY_PATH", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "GTK_PATH", "GIO_MODULE_DIR"]
    for key in keys_to_strip:
        env.pop(key, None)
    return env

def get_brain_env(audio_site_path):
    env = os.environ.copy()
    env["VIRTUAL_ENV"] = VENV_PATH
    env["PATH"] = os.path.join(VENV_PATH, "bin") + os.pathsep + env.get("PATH", "")
    current_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{audio_site_path}{os.pathsep}{current_pp}" if current_pp else audio_site_path
    env["HF_HUB_OFFLINE"] = "1" 
    env["TRANSFORMERS_OFFLINE"] = "1"
    env["CUDA_VISIBLE_DEVICES"] = "-1"
    return env

def check_terminal_installed():
    terms = ["gnome-terminal", "xfce4-terminal", "konsole", "x-terminal-emulator", "xterm"]
    for t in terms:
        if shutil.which(t): return t
    return None

def check_go_installed():
    return shutil.which("go") is not None

# ==============================================================================
# 🧹 SURGICAL CLEANUP
# ==============================================================================
def kill_process_on_port(port):
    try:
        pid = subprocess.check_output(["lsof", "-t", "-i", f":{port}"], stderr=subprocess.DEVNULL).strip().decode()
        if pid:
            log(f" - Port {port} is busy. Killing PID {pid}...", "WARN")
            os.kill(int(pid), signal.SIGKILL)
    except:
        pass

def ensure_clean_slate():
    log("Cleaning ports...", "INFO")
    for port in TARGET_PORTS:
        kill_process_on_port(port)
    time.sleep(0.5)

def ensure_ollama_ready():
    log("Checking LLM Service (Ollama)...", "CHECK")
    try:
        if requests.get(OLLAMA_API_URL, timeout=0.5).status_code == 200:
            return True
    except:
        pass
    log("⚠️ Ollama offline. Starting background service...", "WARN")
    subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    return True

# ==============================================================================
# 🚀 MAIN LOOP
# ==============================================================================
def main():
    os.system("clear")
    print("==========================================")
    print("   SYNTHETA SOVEREIGN BOOTLOADER (V2.9)")
    print("   Mode: Module-Root Build Strategy")
    print("==========================================")

    if not os.path.exists(VENV_PYTHON):
        log(f"Primary Venv missing: {VENV_PYTHON}", "ERROR")
        sys.exit(1)

    audio_site = get_site_packages(VENV_AUDIO_PATH)
    if not audio_site:
        log(f"Audio Venv missing: {VENV_AUDIO_PATH}", "ERROR")
        sys.exit(1)

    if not check_go_installed():
        log("Go is not installed!", "ERROR")
        sys.exit(1)

    term_cmd = check_terminal_installed()
    ensure_ollama_ready()

    # 1. REFRESH KNOWLEDGE (The Librarian Crawler)
    # 🟢 NEW: Run the crawler to update ChromaDB vectors before launching services
    log("Refreshing OMEGA Memory (BGE-M3 Librarian)...", "BOOT")
    crawler_path = os.path.join(PY_DIR, "tools", "code_crawler.py")
    
    if os.path.exists(crawler_path):
        try:
            subprocess.run(
                [VENV_PYTHON, crawler_path], 
                cwd=PY_DIR, 
                check=True
            )
            log("✅ Memory Refresh Successful.", "INFO")
        except subprocess.CalledProcessError as e:
            log(f"⚠️ Knowledge Refresh Failed: {e}", "WARN")
            log("Continuing with existing database...", "INFO")
    else:
        log("Librarian script not found. Skipping memory refresh.", "WARN")

    # 2. COMPILE GO (Corrected for go/cmd structure)
    log("Compiling Go Hub from Module Root...", "BOOT")
    try:
        # 🟢 FIX: Run from 'go/' and target './cmd'
        # This correctly resolves paths relative to go.mod
        subprocess.run(
            ["go", "build", "-o", "syntheta-hub", "./cmd"], 
            cwd=GO_DIR, 
            check=True
        )
        log("✅ Go Compilation Successful.", "INFO")
    except subprocess.CalledProcessError:
        log("❌ Go Compilation Failed. Check go/cmd/main.go", "ERROR")
        sys.exit(1)

    while True:
        try:
            ensure_clean_slate()

            # 4. LAUNCH GO BRIDGE
            log("Launching Audio Bridge (Go)...", "BOOT")
            go_bin = os.path.join(GO_DIR, "syntheta-hub")
            clean_env = get_sanitized_env()

            if term_cmd:
                log(f" - Spawning in {term_cmd}...", "INFO")
                if "gnome-terminal" in term_cmd:
                    subprocess.Popen([term_cmd, "--", go_bin], cwd=GO_DIR, env=clean_env)
                elif "xfce4-terminal" in term_cmd:
                    subprocess.Popen([term_cmd, "-e", go_bin], cwd=GO_DIR, env=clean_env)
                else:
                    subprocess.Popen([term_cmd, "-e", go_bin], cwd=GO_DIR, env=clean_env)
            else:
                subprocess.Popen([go_bin], cwd=GO_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            time.sleep(2) 

            # 5. LAUNCH PYTHON BRAIN
            log(f"Launching Sovereign Brain...", "BOOT")
            print("------------------------------------------")
            
            brain_env = get_brain_env(audio_site)
            brain_process = subprocess.run(
                [VENV_PYTHON, "-u", "main.py"], 
                cwd=PY_DIR,
                env=brain_env
            )

            if brain_process.returncode == 42:
                log("🔄 Restart requested...", "BOOT")
                time.sleep(1)
                continue
            else:
                log("Shutting down.", "INFO")
                break

        except KeyboardInterrupt:
            print("\n")
            log("Manual Interrupt.", "WARN")
            break
        except Exception as e:
            log(f"Launcher Error: {e}", "ERROR")
            break

    ensure_clean_slate()
    print("Goodbye.")

if __name__ == "__main__":
    main()