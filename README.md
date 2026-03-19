# Syntheta Hub

Syntheta Hub is a local-first, low-latency AI orchestration platform for home automation and personal AI assistants. It integrates multiple services (Python brain, Go audio bridge, Web UI) into a cohesive system.

## 🚀 Key Features

- **Sovereign Brain (Python)**: Core logic, ASR (Whisper), TTS (Kokoro), and integration with Ollama/Home Assistant.
- **Audio Bridge (Go)**: High-performance audio stream handling (UDP/TCP).
- **Web UI (HTML/JS)**: Modern, interactive interface for voice interaction.
- **Local-First**: All processing happens on your local hardware.

## 🛠️ Project Structure

- **`python/`**: Brain services, NLU, and orchestration.
- **`go/`**: Low-level audio processing and networking.
- **`webui/`**: Responsive web gateway and frontend.
- **`tests/`**: Experimental and utility scripts.
- **`launcher.py`**: The unifying bootloader that orchestrates all services.

## 🔧 Installation

### 1. Prerequisites
- Python 3.10+
- Go 1.20+
- [Ollama](https://ollama.com/) (installed and running)

### 2. Auto-Setup
Run the automated setup script to create virtual environments, install dependencies, and build the Go bridge:
```bash
chmod +x setup.sh
./setup.sh
```

### 3. Configuration
The setup script creates a `.env` file from the example if it doesn't already exist. Edit it with your Home Assistant details:
```bash
# Edit .env and set HA_URL and HA_TOKEN
nano .env
```

### 4. Running
Start the hub using the loader:
```bash
./run_syntheta.sh
```

## 📜 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
