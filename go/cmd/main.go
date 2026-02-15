package main

import (
	"bufio"
	"eva-hub/dispatcher" // 🟢 IMPORT ADDED: The Event Bus
	"eva-hub/downlink"
	"eva-hub/filler" // 🟢 IMPORT ADDED: The Filler Audio (Ack/Beeps)
	"log"
	"os"
	"strings"
)

func main() {
	log.Println("[BOOT] Starting Syntheta Bridge (Full Nervous System)...")

	// 1. Initialize Satellite Server (The Downlink)
	satServer := downlink.NewSatelliteServer()

	// 2. WIRING: Connect Dispatcher (Logic) to Downlink (Hardware)
	// This ensures that when Python says "Speak", the Bridge knows how to send the audio.
	dispatcher.SendToSat = func(satID int, payload interface{}) {
		// Placeholder: If Go needs to send JSON commands later, we wire it here.
		// For now, Audio is handled directly by dispatcher -> downlink imports.
	}

	// 3. START THE LANES

	// Lane 1: Audio Ingestion (UDP 5555 -> Python 6000)
	go satServer.StartAudioServer("0.0.0.0:5555")

	// Lane 2: Control Stub (UDP 5556 - Legacy/Parity)
	go satServer.StartControlServer("0.0.0.0:5556")

	// Lane 3: Internal Event Bus (TCP 9001) - THE FIX
	// This opens the port so Python can send "Play File" commands.
	go func() {
		log.Println("[BOOT] 🚀 Starting Dispatcher on Port 9001...")
		if err := dispatcher.StartSTTEventListener("0.0.0.0:9001"); err != nil {
			log.Fatalf("❌ Dispatcher Failed: %v", err)
		}
	}()

	// 4. Pre-load Fillers (Optimization)
	go filler.PlayAckActionDone(0)

	// ========================================================
	//  🎮 BRIDGE CONTROLLER
	// ========================================================
	log.Println("================================================")
	log.Println("       SYNTHETA BRIDGE ACTIVE (FULL)            ")
	log.Println(" Lanes: [5555: Audio] [9001: Events]            ")
	log.Println(" [q] QUIT   [r] RESTART                         ")
	log.Println("================================================")

	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		text := strings.TrimSpace(strings.ToLower(scanner.Text()))
		if text == "r" {
			log.Println("🔄 RESTART REQUESTED. Sending signal 42...")
			os.Exit(42)
		}
		if text == "q" {
			log.Println("👋 Quitting...")
			os.Exit(0)
		}
	}
}
