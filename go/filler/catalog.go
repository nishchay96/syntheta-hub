package filler

import (
    "encoding/binary"
    "errors"
    "eva-hub/downlink"
    "io/ioutil"
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

// 🔧 FIX 1: USE RELATIVE PATH (Linux/Windows Compatible)
// Assumes you run the binary from the project root (syntheta-hub/)
var basePath = "./assets/fillers"

func init() {
    rand.Seed(time.Now().UnixNano())
}

func Pick(category Category) (string, error) {
    dir := filepath.Join(basePath, string(category))

    if category == DynamicFiller {
        os.MkdirAll(dir, 0755)
    }

    entries, err := os.ReadDir(dir)
    if err != nil {
        return "", err
    }

    var wavs []string
    for _, e := range entries {
        if e.IsDir() {
            continue
        }
        name := e.Name()
        if strings.HasSuffix(strings.ToLower(name), ".wav") {
            wavs = append(wavs, filepath.Join(dir, name))
        }
    }

    if len(wavs) == 0 {
        return "", errors.New("no wav files found in " + dir)
    }

    return wavs[rand.Intn(len(wavs))], nil
}

func PlayAckActionDone(satID int) {
    playCategory(satID, AckActionDone)
}

func PlayDynamicFiller(satID int, filename string) error {
    path := filepath.Join(basePath, "dynamic", filename)
    return playSafe(satID, path)
}

func playCategory(satID int, cat Category) {
    path, err := Pick(cat)
    if err != nil {
        return
    }
    playSafe(satID, path)
}

func playSafe(satID int, path string) error {
    log.Printf("[FILLER] sat=%d STREAMING ASSET %s\n", satID, filepath.Base(path))

    fileBytes, err := ioutil.ReadFile(path)
    if err != nil {
        log.Printf("[FILLER] Error reading asset: %v", err)
        return err
    }

    pcmData := parseWavBytes(fileBytes)
    if pcmData == nil {
        log.Printf("[FILLER] Invalid WAV format: %s", path)
        return errors.New("invalid wav")
    }

    downlink.PlayPCM(satID, pcmData)
    return nil
}

// Helper: Convert arbitrary WAV bytes to Syntheta Standard PCM (16k MONO)
func parseWavBytes(raw []byte) []byte {
    if len(raw) < 44 {
        return nil
    }
    numChannels := binary.LittleEndian.Uint16(raw[22:24])
    sampleRate := binary.LittleEndian.Uint32(raw[24:28])

    // Find data chunk
    dataStart := 0
    for i := 12; i < len(raw)-8; i++ {
        if raw[i] == 'd' && raw[i+1] == 'a' && raw[i+2] == 't' && raw[i+3] == 'a' {
            dataStart = i + 8
            break
        }
    }
    if dataStart == 0 {
        dataStart = 44
    }
    pcmData := raw[dataStart:]

    inputSamples := downlink.BytesToInt16(pcmData)

    // 🔧 FIX 2: FORCE MONO (Matches player.go logic)
    if numChannels == 2 {
        inputSamples = downlink.StereoToMono(inputSamples)
    }
    
    if sampleRate != 16000 {
        inputSamples = downlink.Resample(inputSamples, int(sampleRate), 16000)
    }

    return downlink.Int16ToBytes(inputSamples)
}