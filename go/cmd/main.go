package main

import (
	"bufio"
	"eva-hub/dispatcher"
	"eva-hub/downlink"
	"eva-hub/filler"
	"log"
	"os"
	"strings"
)

func main() {
	log.Println("[BOOT] Starting Syntheta Bridge (Audio Relay Only)...")

	// 1. Initialize Satellite Server
	satServer := downlink.NewSatelliteServer()

	// 2. Wiring
	// Since Python handles TCP 5556, Go has no direct control lane to the Satellite.
	// We leave this as a no-op placeholder.
	dispatcher.SendToSat = func(satID int, payload interface{}) {
		// Placeholder: Go cannot send TCP commands in this architecture.
		// Use Python's SatelliteNetManager for that.
	}

	// 3. START LANES
	// 🟢 CRITICAL: This line MUST be active for Audio to work!
	// It handles UDP 5555 (Inbound from Mic) -> UDP 6000 (Outbound to Python)
	go satServer.StartAudioServer("0.0.0.0:5555")

	// 🔴 CRITICAL: This line MUST be commented out to free Port 5556 for Python!
	// Python needs exclusive access to the TCP Control Plane.
	// go satServer.StartControlServer("0.0.0.0:5556")

	// 4. Internal Event Bus
	// This listens on TCP 9001 for commands from Python (e.g., "play_topic_filler")
	go func() {
		log.Println("[BOOT] 🚀 Starting Dispatcher on Port 9001...")
		if err := dispatcher.StartSTTEventListener("0.0.0.0:9001"); err != nil {
			log.Fatalf("❌ Dispatcher Failed: %v", err)
		}
	}()

	// 5. Pre-load Assets
	// Plays a silent/dummy sound to force the Asset Loader to initialize
	go filler.PlayAckActionDone(0)

	// ================================================
	log.Println("================================================")
	log.Println("     SYNTHETA BRIDGE ACTIVE (AUDIO ONLY)        ")
	log.Println("     Lanes: [5555: Audio] [9001: Events]        ")
	log.Println("     [5556: RELEASED for Python Brain]          ")
	log.Println("================================================")

	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		text := strings.TrimSpace(strings.ToLower(scanner.Text()))
		if text == "r" {
			log.Println("[CMD] Reboot Requested...")
			os.Exit(42)
		} else if text == "q" {
			log.Println("[CMD] Shutdown Requested...")
			os.Exit(0)
		}
	}
}
