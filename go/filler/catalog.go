package filler

import (
	"errors"
	"eva-hub/downlink"
	"log"
	"math/rand"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// Category defines the folder name in assets/fillers/
type Category string

const (
	AckActionDone Category = "action"
	DynamicFiller Category = "dynamic"
)

// 🔧 PATH CONFIGURATION
// Assumes you run the binary from the project root (syntheta-hub/)
var basePath = "assets/fillers"

func init() {
	rand.Seed(time.Now().UnixNano())
}

// PlayAckActionDone plays a random sound from assets/fillers/action/
func PlayAckActionDone(satID int) {
	playCategory(satID, AckActionDone)
}

// PlayDynamic plays a specific named file from assets/fillers/dynamic/
// 🟢 FIX 1: Renamed from 'PlayDynamicFiller' to match Dispatcher call
func PlayDynamic(satID int, filename string) error {
	// Security: Prevent directory traversal
	cleanName := filepath.Base(filename)
	path := filepath.Join(basePath, "dynamic", cleanName)

	if _, err := os.Stat(path); os.IsNotExist(err) {
		log.Printf("[FILLER] ⚠️ Dynamic Filler not found: %s", path)
		return err
	}

	return playSafe(satID, path)
}

// --- INTERNAL HELPERS ---

func playCategory(satID int, cat Category) {
	path, err := pickRandom(cat)
	if err != nil {
		// It's common for folders to be empty initially, so we just return silently
		return
	}
	playSafe(satID, path)
}

func playSafe(satID int, path string) error {
	log.Printf("[FILLER] 🎭 Sat %d -> Asset: %s", satID, filepath.Base(path))

	// 🟢 OPTIMIZATION:
	// We delegate the heavy lifting to the Downlink engine.
	// It already knows how to read WAV headers, resample, and stream.
	return downlink.PlayAudio(satID, path)
}

func pickRandom(category Category) (string, error) {
	dir := filepath.Join(basePath, string(category))

	// Create directory if it doesn't exist (prevents crashes)
	if _, err := os.Stat(dir); os.IsNotExist(err) {
		os.MkdirAll(dir, 0755)
		return "", errors.New("directory created, empty")
	}

	entries, err := os.ReadDir(dir)
	if err != nil {
		return "", err
	}

	var wavs []string
	for _, e := range entries {
		if !e.IsDir() && strings.HasSuffix(strings.ToLower(e.Name()), ".wav") {
			wavs = append(wavs, filepath.Join(dir, e.Name()))
		}
	}

	if len(wavs) == 0 {
		return "", errors.New("no wav files found")
	}

	return wavs[rand.Intn(len(wavs))], nil
}
