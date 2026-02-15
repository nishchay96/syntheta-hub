package dispatcher

import (
	"bufio"
	"bytes"
	"encoding/json"
	"eva-hub/downlink"
	"eva-hub/filler"
	"log"
	"net"
	"path/filepath"
	"sync"
	"sync/atomic"
	"time"
)

// Global callback to send data down to the Satellite
var SendToSat func(int, interface{})

// TCP Client management
var clients = make(map[net.Conn]bool)
var clientsMu sync.Mutex

// 📊 TELEMETRY GLOBALS
var (
	statsRxCount uint64 = 0 // Incoming from Python
	statsTxCount uint64 = 0 // Outgoing to Python
)

type STTEvent struct {
	Type    string                 `json:"type"`
	SatID   int                    `json:"sat_id"`
	Payload map[string]interface{} `json:"payload"`
}

// 🔧 MONITOR START: Runs in background to log stats
func startTelemetryLogger() {
	ticker := time.NewTicker(10 * time.Second)
	go func() {
		for range ticker.C {
			rx := atomic.SwapUint64(&statsRxCount, 0)
			tx := atomic.SwapUint64(&statsTxCount, 0)

			// Only log if there is traffic to reduce noise
			if rx > 0 || tx > 0 {
				log.Printf("[📊 DISPATCHER] TCP Traffic (10s): RX=%d (Events) | TX=%d (Msgs)", rx, tx)
			}
		}
	}()
}

func StartSTTEventListener(address string) error {
	listener, err := net.Listen("tcp", address)
	if err != nil {
		return err
	}
	log.Printf("[DISPATCHER] 👂 TCP Listener started on %s", address)

	// 🔧 START MONITOR
	startTelemetryLogger()

	go func() {
		for {
			conn, err := listener.Accept()
			if err != nil {
				continue
			}
			clientsMu.Lock()
			clients[conn] = true
			clientsMu.Unlock()
			go handleConnection(conn)
		}
	}()
	return nil
}

func SendToPython(data []byte) {
	clientsMu.Lock()
	defer clientsMu.Unlock()

	// 📊 COUNT TX
	atomic.AddUint64(&statsTxCount, 1)

	// Check if data already has a newline to avoid double spacing
	payload := data
	if !bytes.HasSuffix(data, []byte("\n")) {
		payload = append(data, '\n')
	}

	for conn := range clients {
		go func(c net.Conn) { c.Write(payload) }(conn)
	}
}

func handleConnection(conn net.Conn) {
	defer conn.Close()
	scanner := bufio.NewScanner(conn)
	for scanner.Scan() {
		// 📊 COUNT RX (Raw Lines)
		atomic.AddUint64(&statsRxCount, 1)
		ProcessEvent(scanner.Bytes())
	}
}

// ProcessEvent routes signals from Python -> Go -> Satellite
func ProcessEvent(data []byte) {
	start := time.Now()
	var event STTEvent
	if err := json.Unmarshal(data, &event); err != nil {
		log.Printf("[DISPATCHER] ❌ JSON Decode Error: %v", err)
		return
	}

	// 🔧 FIX: Execute blocking tasks in Goroutines to keep the Dispatcher listening
	// Event names aligned with Python `engine.py` and `stt_event_emitter.py`
	switch event.Type {
	case "wake_ack": // Was "WAKE_ONLY"
		go handleWakeOnly(event, start)
	case "reflex_action": // Was "reflex_action_done"
		go handleReflexDone(event, start)
	case "command": // Was "stt_final"
		go handleCommand(event, start)
	case "play_file":
		// 🔧 CRITICAL: Must be async so we can receive "stop_audio" while playing
		go handlePlayFile(event, start)
	case "stop_audio":
		// 🔧 STOP MUST BE SYNC OR HIGH PRIORITY
		handleStopAudio(event)
	case "play_dynamic": // Was "play_dynamic_filler"
		go handlePlayDynamic(event)
	case "calibration_cmd":
		log.Printf("[DISPATCHER] 🔧 Forwarding Calibration Command to Sat %d", event.SatID)
		if SendToSat != nil {
			SendToSat(event.SatID, event.Payload)
		}
	}
}

func handleWakeOnly(event STTEvent, start time.Time) {
	// ESP32 handles the "Ding" locally now, so this is mostly for logging
	// log.Printf("[MONITOR] ⚡ Wake Ack (Latency: %v) -> SILENT (Trusting ESP)", time.Since(start))
}

func handleReflexDone(event STTEvent, start time.Time) {
	log.Printf("[ACTION] Reflex Executed. Latency: %v", time.Since(start))

	// 1. Tell hardware to stop listening immediately
	if SendToSat != nil {
		cmd := map[string]string{"cmd": "stop_listening"}
		SendToSat(event.SatID, cmd)
	}

	// 2. Play Acknowledgement Sound Immediately
	// This uses the 'filler' package to pick a random confirmation sound
	filler.PlayAckActionDone(event.SatID)
}

func handleCommand(event STTEvent, start time.Time) {
	textRaw, ok := event.Payload["text"]
	if !ok {
		return
	}
	text := textRaw.(string)
	log.Printf("[AI] SLM Request sat=%d text='%s'", event.SatID, text)
}

func handlePlayFile(event STTEvent, start time.Time) {
	pathRaw, ok := event.Payload["filepath"]
	if !ok {
		return
	}
	path := pathRaw.(string)

	// Use filepath.Base for cleaner logs (e.g., "response_123.wav" instead of "/full/path/...")
	log.Printf("[TTS] 🗣️ Playing Generated Response: %s", filepath.Base(path))

	// Calls the robust Downlink engine we fixed in the previous step
	err := downlink.PlayAudio(event.SatID, path)
	if err != nil {
		log.Printf("[DISPATCHER] ❌ Audio Playback Failed: %v", err)
	}
}

func handlePlayDynamic(event STTEvent) {
	filenameRaw, ok := event.Payload["filename"]
	if !ok {
		return
	}
	filename := filenameRaw.(string)

	log.Printf("[CACHE] 🎭 Playing Dynamic Filler: %s", filename)

	// Fillers are small, cached audio files for latency masking
	filler.PlayDynamic(event.SatID, filename)
}

// ✅ HANDLER: BARGE-IN (HUB SIDE ONLY)
func handleStopAudio(event STTEvent) {
	log.Printf("[DISPATCHER] 🛑 STOP_AUDIO received. Killing Hub Stream for Sat %d.", event.SatID)
	// 🟢 FIX: Passed SatID to the downlink engine
	downlink.StopPlayback(event.SatID)
}
