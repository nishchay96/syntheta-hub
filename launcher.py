#!/usr/bin/env python3
import os
import sys
import subprocess
import time
import socket
import shutil
import signal

# ==============================================================================
# ⚙️ CONFIGURATION
# ==============================================================================
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
GO_DIR = os.path.join(ROOT_DIR, "go")
PY_DIR = os.path.join(ROOT_DIR, "python")

# Ports to clean before starting (The "Surgical" List)
TARGET_PORTS = [5555, 5556, 6000, 6002, 9001]

def log(msg, level="INFO"):
    prefix = {"INFO": "ℹ️", "WARN": "⚠️", "ERROR": "❌", "BOOT": "🚀", "CHECK": "🔍"}.get(level, "")
    print(f"[{level}] {prefix} {msg}", flush=True)

# ==============================================================================
# 🔍 UTILS
# ==============================================================================
def find_python_exe():
    # 🟢 UPDATED: Checks the 'audio' folder structure first based on your path
    candidates = [
        os.path.join(PY_DIR, "audio", "venv", "bin", "python"),
        os.path.join(PY_DIR, "venv", "bin", "python"),
        sys.executable
    ]
    for p in candidates:
        if os.path.exists(p): return p
    return sys.executable

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
    keys_to_strip = ["LD_LIBRARY_PATH", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"]
    for key in keys_to_strip:
        env.pop(key, None)
    return env

# ==============================================================================
# 🧹 SURGICAL CLEANUP
# ==============================================================================
def kill_process_on_port(port):
    try:
        # lsof -t -i:PORT returns only the PID
        pid = subprocess.check_output(["lsof", "-t", "-i", f":{port}"], stderr=subprocess.DEVNULL).strip().decode()
        if pid:
            log(f"   - Port {port} is busy. Killing PID {pid}...", "WARN")
            os.kill(int(pid), signal.SIGKILL)
    except:
        pass

def ensure_clean_slate():
    log("Cleaning ports...", "INFO")
    for port in TARGET_PORTS:
        kill_process_on_port(port)
    time.sleep(0.5)

# ==============================================================================
# 🚀 MAIN LOOP
# ==============================================================================
def main():
    os.system("clear")
    print("==========================================")
    print("   SYNTHETA SOVEREIGN BOOTLOADER (LINUX)")
    print("==========================================")

    py_exe = find_python_exe()
    term_cmd = check_terminal_installed()

    if not check_go_installed():
        log("Go is not installed! Run: sudo apt install golang", "ERROR")
        sys.exit(1)

    # 2. COMPILE GO
    log("Compiling Go Hub...", "BOOT")
    try:
        subprocess.run(["go", "build", "-o", "syntheta-hub", "./cmd"], cwd=GO_DIR, check=True)
        log("✅ Go Compilation Successful.", "INFO")
    except subprocess.CalledProcessError:
        log("❌ Go Compilation Failed.", "ERROR")
        sys.exit(1)

    while True:
        try:
            # 3. SURGICAL CLEANUP
            ensure_clean_slate()

            # 4. LAUNCH GO BRIDGE
            log("Launching Audio Bridge (Go)...", "BOOT")
            go_bin = os.path.join(GO_DIR, "syntheta-hub")
            go_bin = os.path.abspath(go_bin)
            
            # 🟢 FIX: USE SANITIZED ENV TO PREVENT CRASHES
            clean_env = get_sanitized_env()

            if term_cmd:
                log(f"   - Spawning in {term_cmd}...", "INFO")
                if "gnome-terminal" in term_cmd:
                    # Gnome terminal needs the clean env to avoid libpthread crash
                    subprocess.Popen([term_cmd, "--", go_bin], cwd=GO_DIR, env=clean_env)
                elif "xfce4-terminal" in term_cmd:
                    subprocess.Popen([term_cmd, "-e", go_bin], cwd=GO_DIR, env=clean_env)
                elif "xterm" in term_cmd:
                    subprocess.Popen([term_cmd, "-e", go_bin], cwd=GO_DIR, env=clean_env)
                else:
                    # Generic fallback
                    subprocess.Popen([term_cmd, "-e", go_bin], cwd=GO_DIR, env=clean_env)
            else:
                log("⚠️ No terminal emulator found. Running Go in background.", "WARN")
                subprocess.Popen([go_bin], cwd=GO_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            time.sleep(2) 

            # 5. LAUNCH PYTHON BRAIN (IN CURRENT TERMINAL)
            log(f"Launching Sovereign Brain using {py_exe}...", "BOOT")
            print("------------------------------------------")
            
            # Python keeps the current environment (Venv is good here)
            brain_process = subprocess.run([py_exe, "-u", "main.py"], cwd=PY_DIR)

            # 6. EXIT HANDLING
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