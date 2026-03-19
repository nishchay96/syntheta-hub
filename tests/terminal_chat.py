import os
import sys
import time
from unittest.mock import MagicMock

# Setup Paths
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.join(PROJECT_ROOT, 'python')
if PYTHON_DIR not in sys.path:
    sys.path.insert(0, PYTHON_DIR)

from services.engine import SynthetaEngine
from services.state_manager import EngineState
from services.web_api import get_web_server

def run_terminal_mode():
    print("===================================================")
    print("🧠 BOOTING SYNTHETA TEXT-MODE (NO ESP REQUIRED)")
    print("===================================================\n")
    
    # 🟢 Configure Web Server for UI Access in Terminal Mode *BEFORE* heavy models boot
    uvicorn_server, ws_manager = get_web_server(engine=None, port=8000)
    
    def boot_and_run_cli():
        state = EngineState()
        pi_mock = MagicMock()
        pi_mock.process_query.return_value = None # Bypass hardware actions
        
        # Initialize the actual engine (Heavy Model Loading)
        engine = SynthetaEngine(state_manager=state, pi_manager=pi_mock)
        
        # Intercept her voice output so it prints cleanly to the terminal instead
        engine._speak = lambda sid, text, force_listen=False, telemetry=None: print(f"\n💬 SYNTHETA SAYS: {text}\n")        
        sat_id = 1
        engine.state.is_conversation = True
        
        print("✅ Engine Ready. Type your message below (or 'exit' to quit).")
        print("="*51)
        
        # Register the previously configured web manager and engine reference
        engine.register_web_manager(ws_manager)
        import services.web_api as web_api
        web_api.syntheta_engine_ref = engine
    

        while True:
            try:
                # 🟢 FIX: Tell NightWatchman the mic is closed so it can weave memories!
                engine.state.session_mode[sat_id] = "IDLE" 
                
                user_input = input("\n🗣️ YOU: ")
                
                if user_input.lower() in ['exit', 'quit']:
                    print("Shutting down simulation...")
                    # Hard exit since Uvicorn is holding the main thread
                    os._exit(0)
                if not user_input.strip():
                    continue
                
                # 🟢 FIX: Lock the mic state while Llama is generating
                engine.state.session_mode[sat_id] = "LISTENING"
                telemetry = {"start_time": time.perf_counter()} 
                
                # Feed directly into the command handler, skipping the microphone
                engine.handle_input(sat_id, user_input, telemetry)
                
                # 🟢 CRITICAL: Give the background NightWatchman a second to catch the job
                time.sleep(1)
                
            except KeyboardInterrupt:
                print("\nShutting down simulation...")
                os._exit(0)

    import threading
    threading.Thread(target=boot_and_run_cli, daemon=True).start()
    
    # Pass the main thread cleanly to Uvicorn so it handles network I/O
    import asyncio
    try:
        asyncio.run(uvicorn_server.serve())
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    run_terminal_mode()