#!/usr/bin/env python3
import os
import sys
import subprocess
import time
import shutil
import signal
import requests

# ==============================================================================
# ⚙️ CONFIGURATION
# ==============================================================================
# 🛑 ROOT LOCATION (Strictly Enforced)
ROOT_DIR = "/media/nishchay/Study/syntheta-hub"

# 🛑 VENV LOCATION (The one with Kokoro & Brain installed)
VENV_PATH = os.path.join(ROOT_DIR, "venv")
VENV_PYTHON = os.path.join(VENV_PATH, "bin", "python")

# Service Directories
GO_DIR = os.path.join(ROOT_DIR, "go")
PY_DIR = os.path.join(ROOT_DIR, "python") # main.py is here

# Ports to clean
TARGET_PORTS = [5555, 5556, 6000, 6002, 9001]
OLLAMA_API_URL = "http://localhost:11434/api/tags"

def log(msg, level="INFO"):
    prefix = {"INFO": "ℹ️", "WARN": "⚠️", "ERROR": "❌", "BOOT": "🚀", "CHECK": "🔍"}.get(level, "")
    print(f"[{level}] {prefix} {msg}", flush=True)

# ==============================================================================
# 🔍 ENVIRONMENT UTILS
# ==============================================================================
def verify_venv():
    """Ensures we are using the correct Python with ALL dependencies."""
    if not os.path.exists(VENV_PYTHON):
        log(f"❌ Virtual Environment missing at: {VENV_PYTHON}", "ERROR")
        sys.exit(1)
    
    # Check for BOTH Voice (Kokoro) and Brain (SentenceTransformers) libs
    try:
        subprocess.check_call(
            [VENV_PYTHON, "-c", "import kokoro; import sentence_transformers"], 
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL
        )
        log("✅ Voice & Brain dependencies verified.", "INFO")
    except subprocess.CalledProcessError:
        log("❌ Dependencies missing! Activate venv and run:", "ERROR")
        log("   pip install kokoro soundfile numpy sentence-transformers qdrant-client requests ollama", "ERROR")
        sys.exit(1)
    return VENV_PYTHON

def check_go_installed():
    return shutil.which("go") is not None

def check_terminal_installed():
    # Priority list for Linux Terminals
    terms = ["gnome-terminal", "xfce4-terminal", "konsole", "x-terminal-emulator", "xterm"]
    for t in terms:
        if shutil.which(t): return t
    return None

def get_sanitized_env():
    """
    Creates a clean environment for external system apps (like gnome-terminal).
    Removes Python Venv pollution that causes 'symbol lookup errors'.
    """
    env = os.environ.copy()
    # Strip dangerous variables that confuse system apps
    keys_to_strip = ["LD_LIBRARY_PATH", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "GTK_PATH", "GIO_MODULE_DIR"]
    for key in keys_to_strip:
        env.pop(key, None)
    return env

def get_brain_env():
    """Sets up the environment specifically for the Python Brain."""
    env = os.environ.copy()
    # 🔧 FIX: Manually 'activate' the venv for the subprocess
    env["VIRTUAL_ENV"] = VENV_PATH
    env["PATH"] = os.path.join(VENV_PATH, "bin") + os.pathsep + env.get("PATH", "")
    # Kokoro needs offline mode enforced
    env["HF_HUB_OFFLINE"] = "1" 
    return env

# ==============================================================================
# 🚀 OLLAMA WARM-UP & CLEANUP
# ==============================================================================
def ensure_ollama_ready():
    log("Checking LLM Service (Ollama)...", "CHECK")
    try:
        if requests.get(OLLAMA_API_URL, timeout=0.5).status_code == 200:
            log("✅ LLM Service is Online.", "INFO")
            return True
    except:
        pass
    log("⚠️ Ollama offline. Starting background service...", "WARN")
    subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    return True

def kill_process_on_port(port):
    try:
        pid = subprocess.check_output(["lsof", "-t", "-i", f":{port}"], stderr=subprocess.DEVNULL).strip().decode()
        if pid:
            log(f"   - Port {port} busy. Killing PID {pid}...", "WARN")
            os.kill(int(pid), signal.SIGKILL)
    except:
        pass

def ensure_clean_slate():
    for port in TARGET_PORTS:
        kill_process_on_port(port)
    time.sleep(0.5)

# ==============================================================================
# 🚀 MAIN MONITOR LOOP
# ==============================================================================
def main():
    os.system("clear")
    print("==========================================")
    print("   SYNTHETA SOVEREIGN BOOTLOADER (V2.3)")
    print("   Target Venv: syntheta-hub/venv")
    print("==========================================")

    # 1. VERIFY VENV & DEPENDENCIES
    py_exe = verify_venv()

    if not check_go_installed():
        log("Go not installed. Run: sudo apt install golang", "ERROR")
        sys.exit(1)

    term_cmd = check_terminal_installed()
    ensure_ollama_ready()

    # 2. COMPILE GO
    log("Compiling Go Hub...", "BOOT")
    try:
        subprocess.run(["go", "build", "-o", "syntheta-hub", "."], cwd=os.path.join(GO_DIR, "cmd"), check=True)
    except Exception:
        try:
            subprocess.run(["go", "build", "-o", "syntheta-hub", "."], cwd=GO_DIR, check=True)
        except:
            log("❌ Go Compilation Failed.", "ERROR")
            sys.exit(1)

    while True:
        try:
            # 3. SURGICAL CLEANUP
            ensure_clean_slate()

            # 4. LAUNCH GO BRIDGE
            log("Launching Audio Bridge...", "BOOT")
            go_bin = os.path.join(GO_DIR, "syntheta-hub") 
            if not os.path.exists(go_bin): 
                 go_bin = os.path.join(GO_DIR, "cmd", "syntheta-hub")

            # 🟢 FIX: Use Sanitized Env to prevent Gnome-Terminal crashes
            clean_env = get_sanitized_env()

            if term_cmd:
                log(f"   - Spawning in {term_cmd}...", "INFO")
                if "gnome-terminal" in term_cmd:
                    subprocess.Popen([term_cmd, "--", go_bin], cwd=GO_DIR, env=clean_env)
                elif "xfce4-terminal" in term_cmd:
                    subprocess.Popen([term_cmd, "-e", go_bin], cwd=GO_DIR, env=clean_env)
                elif "xterm" in term_cmd:
                    subprocess.Popen([term_cmd, "-e", go_bin], cwd=GO_DIR, env=clean_env)
                else:
                    # Fallback for others
                    subprocess.Popen([term_cmd, "-e", go_bin], cwd=GO_DIR, env=clean_env)
            else:
                log("⚠️ No terminal emulator. Running Go in background.", "WARN")
                subprocess.Popen([go_bin], cwd=GO_DIR, stdout=subprocess.DEVNULL)

            time.sleep(2) 

            # 5. LAUNCH PYTHON BRAIN
            log(f"Launching Brain...", "BOOT")
            print("------------------------------------------")
            
            # Python keeps the Venv environment
            brain_process = subprocess.run(
                [py_exe, "-u", "main.py"], 
                cwd=PY_DIR,
                env=get_brain_env() 
            )

            # 6. RESTART HANDLING
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