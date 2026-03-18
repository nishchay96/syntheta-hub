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

func init() {
	downlink.OnPlaybackFinished = func(satID int, filename string) {
		event := STTEvent{
			Type:  "playback_finished",
			SatID: satID,
			Payload: map[string]interface{}{
				"file": filename,
			},
		}
		data, err := json.Marshal(event)
		if err == nil {
			baseName := filepath.Base(filename)
			log.Printf("[DISPATCHER] 📢 Notifying Python: Playback Finished (Sat %d) | File: %s", satID, baseName)
			SendToPython(data)
		}
	}
}

var SendToSat func(int, interface{})

var clients = make(map[net.Conn]bool)
var clientsMu sync.Mutex

// 🟢 NEW: Sequence Tracking for loop cancellation
var thinkingSeqMu sync.Mutex
var thinkingSeq = make(map[int]int64)

var (
	statsRxCount uint64 = 0
	statsTxCount uint64 = 0
)

type STTEvent struct {
	Type    string                 `json:"type"`
	SatID   int                    `json:"sat_id"`
	Payload map[string]interface{} `json:"payload"`
}

func startTelemetryLogger() {
	ticker := time.NewTicker(10 * time.Second)
	go func() {
		for range ticker.C {
			rx := atomic.SwapUint64(&statsRxCount, 0)
			tx := atomic.SwapUint64(&statsTxCount, 0)
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

	atomic.AddUint64(&statsTxCount, 1)

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
		atomic.AddUint64(&statsRxCount, 1)
		ProcessEvent(scanner.Bytes())
	}
}

func ProcessEvent(data []byte) {
	start := time.Now()
	var event STTEvent
	if err := json.Unmarshal(data, &event); err != nil {
		log.Printf("[DISPATCHER] ❌ JSON Decode Error: %v", err)
		return
	}

	switch event.Type {
	case "wake_ack":
		go handleWakeOnly(event, start)
	case "reflex_action":
		go handleReflexDone(event, start)
	case "command":
		go handleCommand(event, start)
	case "play_file":
		go handlePlayFile(event, start)
	case "stop_audio":
		handleStopAudio(event)
	case "start_thinking_audio": // 🟢 NEW: Unified routing event
		go handleStartThinkingAudio(event)
	case "calibration_cmd":
		log.Printf("[DISPATCHER] 🔧 Forwarding Calibration Command to Sat %d", event.SatID)
		if SendToSat != nil {
			SendToSat(event.SatID, event.Payload)
		}
	}
}

// 🟢 NEW: The Smart Audio Sequence Engine
func handleStartThinkingAudio(event STTEvent) {
	topicRaw, ok1 := event.Payload["topic"]
	playFillerRaw, ok2 := event.Payload["play_filler"]
	if !ok1 || !ok2 {
		return
	}

	topic := topicRaw.(string)
	playFiller := playFillerRaw.(bool)

	// Register a new sequence ID
	seq := time.Now().UnixNano()
	thinkingSeqMu.Lock()
	thinkingSeq[event.SatID] = seq
	thinkingSeqMu.Unlock()

	// Step 1: Conditionally play filler & wait
	if playFiller {
		log.Printf("[THINKING] 🎭 Topic changed to '%s'. Playing filler.", topic)
		filler.PlayTopicFiller(event.SatID, topic) // Blocks until file is done or cancelled

		// Check if TTS killed us mid-filler
		thinkingSeqMu.Lock()
		currentSeq := thinkingSeq[event.SatID]
		thinkingSeqMu.Unlock()
		if currentSeq != seq {
			return
		}

		time.Sleep(200 * time.Millisecond) // The human pause

		// Check again before bridging
		thinkingSeqMu.Lock()
		currentSeq = thinkingSeq[event.SatID]
		thinkingSeqMu.Unlock()
		if currentSeq != seq {
			return
		}
	} else {
		log.Printf("[THINKING] 🔄 Topic '%s' unchanged. Skipping filler, straight to bridge.", topic)
	}

	// Step 2: Loop Bridge Audio
	log.Printf("[THINKING] 🎵 Starting bridge audio loop.")
	for {
		thinkingSeqMu.Lock()
		currentSeq := thinkingSeq[event.SatID]
		thinkingSeqMu.Unlock()

		// If ID changed (TTS arrived or user said Stop), collapse the loop instantly
		if currentSeq != seq {
			break
		}

		err := filler.PlayDynamic(event.SatID, "bridge.wav")
		if err != nil {
			time.Sleep(500 * time.Millisecond) // Prevent CPU spin if file is missing
		}
	}
}

func handleWakeOnly(event STTEvent, start time.Time) {}

func handleReflexDone(event STTEvent, start time.Time) {
	// Kill loop if active
	thinkingSeqMu.Lock()
	thinkingSeq[event.SatID] = 0
	thinkingSeqMu.Unlock()

	log.Printf("[ACTION] Reflex Executed. Latency: %v", time.Since(start))
	if SendToSat != nil {
		cmd := map[string]string{"cmd": "stop_listening"}
		SendToSat(event.SatID, cmd)
	}
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
	// 🟢 KILL SWITCH: Instantly collapses the thinking loop before playing TTS
	thinkingSeqMu.Lock()
	thinkingSeq[event.SatID] = 0
	thinkingSeqMu.Unlock()

	pathRaw, ok := event.Payload["filepath"]
	if !ok {
		return
	}
	path := pathRaw.(string)

	log.Printf("[TTS] 🗣️ Playing Generated Response: %s", filepath.Base(path))

	err := downlink.PlayAudio(event.SatID, path)
	if err != nil {
		log.Printf("[DISPATCHER] ❌ Audio Playback Failed: %v", err)
	}
}

func handleStopAudio(event STTEvent) {
	// 🟢 KILL SWITCH: Stop loop on barge-in/cancel
	thinkingSeqMu.Lock()
	thinkingSeq[event.SatID] = 0
	thinkingSeqMu.Unlock()

	log.Printf("[DISPATCHER] 🛑 STOP_AUDIO received. Killing Hub Stream for Sat %d.", event.SatID)
	downlink.StopPlayback(event.SatID)
}
