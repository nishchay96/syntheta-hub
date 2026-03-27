#!/usr/bin/env python3
import os
import sys
import subprocess
import time
import shutil
import signal
import glob
import logging # Corrected import
import requests
import sqlite3

# ==============================================================================
# ⚙️ CONFIGURATION
# ==============================================================================
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

# Venv Paths
VENV_PATH = os.path.join(ROOT_DIR, "venv")
VENV_PYTHON = os.path.join(VENV_PATH, "bin", "python")
VENV_AUDIO_PATH = os.path.join(ROOT_DIR, "venv-audio")

# Service Directories
GO_DIR = os.path.join(ROOT_DIR, "go")
PY_DIR = os.path.join(ROOT_DIR, "python")

# The Surgical List (Added Port 8000 for the Web Gateway)
TARGET_PORTS = [5555, 5556, 6000, 6002, 8000, 8001, 9001]
OLLAMA_API_URL = "http://localhost:11434/api/tags"

# Log Management
LOG_DIR = os.path.join(ROOT_DIR, "assets", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE_PATH = os.path.join(LOG_DIR, "syntheta.log")

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
    env["CHROMA_TELEMETRY"] = "false" 
    
    return env

def check_terminal_installed():
    terms = ["gnome-terminal", "xfce4-terminal", "konsole", "x-terminal-emulator", "xterm"]
    for t in terms:
        if shutil.which(t): return t
    return None

def can_use_gui_terminal():
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

def launch_audio_bridge(go_bin, clean_env, term_cmd):
    if term_cmd and can_use_gui_terminal():
        log(f" - Spawning in {term_cmd}...", "INFO")
        try:
            if "gnome-terminal" in term_cmd:
                proc = subprocess.Popen([term_cmd, "--", go_bin], cwd=GO_DIR, env=clean_env)
            elif "xfce4-terminal" in term_cmd:
                proc = subprocess.Popen([term_cmd, "-e", go_bin], cwd=GO_DIR, env=clean_env)
            else:
                proc = subprocess.Popen([term_cmd, "-e", go_bin], cwd=GO_DIR, env=clean_env)

            time.sleep(0.5)
            if proc.poll() is None:
                return

            log(f"GUI terminal exited immediately with code {proc.returncode}. Falling back to headless launch.", "WARN")
        except Exception as e:
            log(f"GUI terminal launch failed: {e}. Falling back to headless launch.", "WARN")
    elif term_cmd:
        log(" - GUI terminal detected but no graphical session is available. Falling back to headless launch.", "WARN")

    subprocess.Popen([go_bin], cwd=GO_DIR, env=clean_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def check_go_installed():
    return shutil.which("go") is not None

# ==============================================================================
# 🧹 SURGICAL CLEANUP
# ==============================================================================
def kill_process_on_port(port):
    try:
        pids = subprocess.check_output(["lsof", "-t", "-i", f":{port}"], stderr=subprocess.DEVNULL).strip().decode()
        if pids:
            for pid in pids.split('\n'):
                pid = pid.strip()
                if pid:
                    log(f" - Port {port} is busy. Killing PID {pid}...", "WARN")
                    os.kill(int(pid), signal.SIGKILL)
    except:
        pass

def ensure_clean_slate(internal_restart=False):
    log("Cleaning ports...", "INFO")
    for port in TARGET_PORTS:
        # Skip Web UI/Gateway ports during internal backend restarts
        if internal_restart and port in [8000, 8001]:
            continue
        kill_process_on_port(port)
        
    db_path = os.path.join(ROOT_DIR, "assets", "database", "syntheta_ledger.db")
    if os.path.exists(db_path):
        log("Flushing Memory Queue & OpenClaw Backlog...", "INFO")
        try:
            # Native Python execution instead of OS subprocess
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM memory_queue;")
                cursor.execute("DELETE FROM openclaw_jobs;")
                conn.commit()
            log("✅ SQLite Backlog Cleared.", "INFO")
        except Exception as e:
            log(f"Failed to clear backlog: {e}", "WARN")

def tail_to_console(stop_event):
    """Tails the log file to stdout so it's visible in the physical terminal."""
    # Wait for file to exist
    while not os.path.exists(LOG_FILE_PATH) and not stop_event.is_set():
        time.sleep(0.5)
    
    with open(LOG_FILE_PATH, 'r', encoding='utf-8', errors='ignore') as f:
        # Seek to end so we don't dump old logs on restart
        f.seek(0, os.SEEK_END)
        while not stop_event.is_set():
            line = f.readline()
            if not line:
                time.sleep(0.1)
                continue
            print(line, end='', flush=True)

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
    import threading
    stop_tail = threading.Event()
    threading.Thread(target=tail_to_console, args=(stop_tail,), daemon=True).start()

    os.system("clear")
    print("==========================================")
    print("   SYNTHETA SOVEREIGN BOOTLOADER (V3.0)")
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

    log("Compiling Go Hub from Module Root...", "BOOT")
    try:
        subprocess.run(
            ["go", "build", "-o", "syntheta-hub", "./cmd"], 
            cwd=GO_DIR, 
            check=True
        )
        log("✅ Go Compilation Successful.", "INFO")
    except subprocess.CalledProcessError:
        log("❌ Go Compilation Failed. Check go/cmd/main.go", "ERROR")
        sys.exit(1)

    # 1. PRE-FLIGHT CLEANUP (Full)
    ensure_clean_slate(internal_restart=False)
    ensure_ollama_ready()

    # 2. PERSISTENT WEB GATEWAY (Port 8001)
    # This process survives Brain restarts (return code 42)
    log("Launching Persistent Web Gateway on Port 8001...", "BOOT")
    gateway_log = open(os.path.join(LOG_DIR, "gateway.log"), "a", encoding="utf-8")
    gateway_process = subprocess.Popen(
        [VENV_PYTHON, "-m", "uvicorn", "python.web_gateway:app", "--host", "0.0.0.0", "--port", "8001", "--no-access-log"],
        cwd=ROOT_DIR,
        stdout=gateway_log,
        stderr=gateway_log
    )

    while True:
        # Open log file for brain in append mode
        log_file = open(LOG_FILE_PATH, "a", encoding="utf-8")
        log_file.write(f"\n\n--- SYNTHETA SOVEREIGN BRAIN SESSION [{time.strftime('%Y-%m-%d %H:%M:%S')}] ---\n")
        log_file.flush()

        try:
            # Only clean backend ports during internal restart
            ensure_clean_slate(internal_restart=True)


            # 2. LAUNCH GO BRIDGE
            log("Launching Audio Bridge (Go)...", "BOOT")
            go_bin = os.path.join(GO_DIR, "syntheta-hub")
            clean_env = get_sanitized_env()

            launch_audio_bridge(go_bin, clean_env, term_cmd)

            time.sleep(2) 

            # 3. LAUNCH PYTHON BRAIN (RESTORED)
            log(f"Launching Sovereign Brain...", "BOOT")
            print("------------------------------------------")
            
            brain_env = get_brain_env(audio_site)
            brain_process = subprocess.run(
                [VENV_PYTHON, "-u", "main.py"], 
                cwd=PY_DIR,
                env=brain_env,
                stdout=log_file,
                stderr=log_file
            )

            # 4. EXIT HANDLING
            if brain_process.returncode == 42:
                log("🔄 Brain Restart requested (Gateway persists)...", "BOOT")
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

    # FINAL CLEANUP
    if gateway_process:
        gateway_process.terminate()
    ensure_clean_slate(internal_restart=False)
    print("Goodbye.")

if __name__ == "__main__":
    main()
