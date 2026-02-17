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
	dispatcher.SendToSat = func(satID int, payload interface{}) {
		// Placeholder
	}

	// 3. START LANES
	// 🟢 CRITICAL: This line MUST be active for Audio to work!
	go satServer.StartAudioServer("0.0.0.0:5555")

	// 🔴 CRITICAL: This line MUST be commented out to free Port 5556 for Python!
	// go satServer.StartControlServer("0.0.0.0:5556")

	// 4. Internal Event Bus
	go func() {
		log.Println("[BOOT] 🚀 Starting Dispatcher on Port 9001...")
		if err := dispatcher.StartSTTEventListener("0.0.0.0:9001"); err != nil {
			log.Fatalf("❌ Dispatcher Failed: %v", err)
		}
	}()

	// 5. Pre-load
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
			os.Exit(42)
		} else if text == "q" {
			os.Exit(0)
		}
	}
}
