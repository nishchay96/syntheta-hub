package filler

import (
	"errors"
	"eva-hub/downlink"
	"log"
	"math/rand"
	"os"
	"path/filepath"
	"strings"
)

// Category defines the folder name in assets/fillers/
type Category string

const (
	AckActionDone Category = "action"
	DynamicFiller Category = "dynamic"

	// Topic-Based Categories
	Scientific Category = "scientific"
	Fictional  Category = "fictional"
	Emotional  Category = "emotional"
	Political  Category = "political"
	Technical  Category = "technical"
	General    Category = "general"
	Help       Category = "help"

	// System Voice Notes
	SystemNote Category = "../system"
)

// 🔧 PATH CONFIGURATION
var basePath string

// Inside catalog.go
func init() {
	// Force the path to the one you confirmed with 'ls'
	basePath = "/media/nishchay/Study/syntheta-hub/assets/fillers"
	log.Printf("[FILLER] 📂 Asset Base Path LOCKED to: %s", basePath)
}

// PlayAckActionDone plays a random sound from assets/fillers/action/
func PlayAckActionDone(satID int) {
	playCategory(satID, AckActionDone)
}

// PlayTopicFiller picks a random filler based on the Semantic Brain's classification
func PlayTopicFiller(satID int, topic string) {
	cat := Category(strings.ToLower(topic))
	playCategory(satID, cat)
}

// PlaySystemNote plays a specific system announcement
func PlaySystemNote(satID int, noteName string) {
	path := filepath.Join(basePath, string(SystemNote), noteName+".wav")
	playSafe(satID, path)
}

// PlayDynamic plays a specific named file from assets/fillers/dynamic/
func PlayDynamic(satID int, filename string) error {
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
		// log.Printf("[FILLER] ⚠️ Could not find asset in category %s: %v", cat, err)
		return
	}
	playSafe(satID, path)
}

func playSafe(satID int, path string) error {
	if _, err := os.Stat(path); os.IsNotExist(err) {
		log.Printf("[FILLER] ❌ File Missing: %s", path)
		return err
	}
	log.Printf("[FILLER] 🎭 Sat %d -> Streaming Topic Asset: %s", satID, filepath.Base(path))
	return downlink.PlayAudio(satID, path)
}

func pickRandom(category Category) (string, error) {
	dir := filepath.Join(basePath, string(category))

	// Create directory if it doesn't exist (prevents crashes)
	if _, err := os.Stat(dir); os.IsNotExist(err) {
		return "", errors.New("directory does not exist")
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
