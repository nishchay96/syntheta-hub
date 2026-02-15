#!/usr/bin/env bash

# ========================================================
# SYNTHETA LINUX LOADER
# Wraps launcher.py to ensure environment is correct.
# ========================================================

# 1. CRITICAL: SET WORKING DIRECTORY
# Resolves the directory where this script actually lives
cd "$(dirname "$0")" || exit 1
HUB_ROOT=$(pwd)

# 2. FIND PYTHON INTERPRETER
# Matches logic in launcher.py to ensure consistent execution
HUB_PY_DIR="$HUB_ROOT/python"
HUB_PY_EXE=""

find_python() {
    # A. Check Audio Venv (Highest Priority)
    if [ -x "$HUB_PY_DIR/audio/venv/bin/python" ]; then
        echo "$HUB_PY_DIR/audio/venv/bin/python"
        return
    fi
    
    # B. Check General Venv
    if [ -x "$HUB_PY_DIR/venv/bin/python" ]; then
        echo "$HUB_PY_DIR/venv/bin/python"
        return
    fi
    
    # C. Fallback to System
    if command -v python3 &> /dev/null; then
        command -v python3
        return
    fi
    
    if command -v python &> /dev/null; then
        command -v python
        return
    fi
}

HUB_PY_EXE=$(find_python)

# 3. VALIDATE PYTHON
if [ -z "$HUB_PY_EXE" ]; then
    echo "[CRITICAL] No Python interpreter found!"
    echo "Please install python3 or create a venv in python/audio/venv/"
    exit 1
fi

# 4. PRE-FLIGHT CHECKS
if [ ! -f "launcher.py" ]; then
    echo "[CRITICAL] launcher.py not found in $HUB_ROOT"
    echo "Make sure you run this script from the syntheta-hub folder."
    exit 1
fi

# Auto-fix permissions
chmod +x launcher.py 2>/dev/null
chmod +x "$HUB_PY_DIR/main.py" 2>/dev/null

# 5. EXECUTE
echo "[LOADER] Working Dir:  $HUB_ROOT"
echo "[LOADER] Using Python: $HUB_PY_EXE"
echo "[LOADER] Starting Syntheta Launcher..."
echo "------------------------------------------"

# Execute the launcher, passing any args
"$HUB_PY_EXE" launcher.py "$@"
EXIT_CODE=$?

# 6. EXIT HANDLING
if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "------------------------------------------"
    echo "[CRITICAL] Launcher exited with code $EXIT_CODE"
    echo "Press Enter to close..."
    read -r
fi

exit $EXIT_CODE