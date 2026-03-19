package downlink

import (
	"context"
	"encoding/binary"
	"io/ioutil"
	"log"
	"net"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// --- GLOBAL STATE ---
var (
	GlobalSatServer *SatelliteServer
	streamMu        sync.RWMutex
	activeStreams   = make(map[int]context.CancelFunc)

	// OnPlaybackFinished is the global hook used by the dispatcher to notify Python
	OnPlaybackFinished func(int, string)
)

// SatelliteServer handles UDP audio routing and learned IP mapping
type SatelliteServer struct {
	audioListener    *net.UDPConn
	activeSatellites map[int]*net.UDPAddr // Maps SatID -> IP/Port
	mu               sync.RWMutex
}

// NewSatelliteServer initializes the global singleton
func NewSatelliteServer() *SatelliteServer {
	server := &SatelliteServer{
		activeSatellites: make(map[int]*net.UDPAddr),
	}
	GlobalSatServer = server
	return server
}

// StartAudioServer listens for incoming UDP audio and learns routes
func (s *SatelliteServer) StartAudioServer(address string) {
	addr, err := net.ResolveUDPAddr("udp", address)
	if err != nil {
		log.Fatalf("[DOWNLINK] ❌ Failed to resolve UDP address: %v", err)
	}

	conn, err := net.ListenUDP("udp", addr)
	if err != nil {
		log.Fatalf("[DOWNLINK] ❌ Failed to bind UDP port: %v", err)
	}

	s.audioListener = conn
	log.Printf("[DOWNLINK] 🚀 Audio Relay Listening on %s", address)

	// 🔧 STATIC ROUTE: Aligning with Router Network
	s.mu.Lock()
	if manualAddr, err := net.ResolveUDPAddr("udp", "192.168.1.103:5555"); err == nil {
		s.activeSatellites[1] = manualAddr
		log.Printf("[DOWNLINK] 🔧 STATIC ROUTE ESTABLISHED: Sat 1 -> %s", manualAddr.String())
	}
	s.mu.Unlock()

	buffer := make([]byte, 2048)
	pythonAddr, _ := net.ResolveUDPAddr("udp", "127.0.0.1:6000")

	for {
		n, remoteAddr, err := conn.ReadFromUDP(buffer)
		if err != nil {
			continue
		}

		if n >= 1 {
			satID := int(buffer[0])
			s.mu.Lock()
			if existing, ok := s.activeSatellites[satID]; !ok || existing.String() != remoteAddr.String() {
				log.Printf("[DOWNLINK] 📡 Learned Route: Sat %d -> %s", satID, remoteAddr.String())
			}
			s.activeSatellites[satID] = remoteAddr
			s.mu.Unlock()

			if n > 1 {
				conn.WriteToUDP(buffer[:n], pythonAddr)
			}
		}
	}
}

func (s *SatelliteServer) StartControlServer(address string) {
	log.Printf("[DOWNLINK] ⚠️ Control Server (TCP) is managed by Python directly.")
}

func StopPlayback(satID int) {
	streamMu.Lock()
	defer streamMu.Unlock()

	if cancel, exists := activeStreams[satID]; exists {
		log.Printf("[DOWNLINK] 🛑 KILL SIGNAL RECEIVED for Sat %d. Stopping Audio.", satID)
		cancel()
		delete(activeStreams, satID)
	}
}

// PlayAudio processes a WAV file and streams it to the satellite
func PlayAudio(satID int, inputPath string) error {
	// 🟢 FIX: Absolute Path Resolver
	// This anchors all relative paths to the confirmed project root
	projectRoot := ".."
	finalPath := inputPath

	if !filepath.IsAbs(inputPath) {
		finalPath = filepath.Join(projectRoot, inputPath)
	}

	// 🟢 DELETED: The `defer` block that was ruthlessly deleting the file has been removed.
	// File lifecycle is now purely managed by Python.

	file, err := os.Open(finalPath)
	if err != nil {
		log.Printf("[DOWNLINK] ❌ Error opening file at %s: %v", finalPath, err)
		return err
	}

	raw, err := ioutil.ReadAll(file)
	file.Close()
	if err != nil {
		return err
	}

	if len(raw) < 44 {
		return nil
	}

	numChannels := binary.LittleEndian.Uint16(raw[22:24])
	sampleRate := binary.LittleEndian.Uint32(raw[24:28])

	// Find 'data' chunk
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
	if dataStart >= len(raw) {
		return nil
	}

	pcmData := raw[dataStart:]
	inputSamples := BytesToInt16(pcmData)

	// Audio Hygiene (Resampling and Channel Mix)
	if numChannels == 2 {
		inputSamples = StereoToMono(inputSamples)
	}
	if sampleRate != 16000 {
		inputSamples = Resample(inputSamples, int(sampleRate), 16000)
	}

	// 🔉 50% VOLUME ALIGNMENT
	for i := range inputSamples {
		inputSamples[i] = int16(float64(inputSamples[i]) * 0.5)
	}
	finalBytes := Int16ToBytes(inputSamples)

	streamMu.Lock()
	if cancel, exists := activeStreams[satID]; exists {
		cancel()
	}
	ctx, cancel := context.WithCancel(context.Background())
	activeStreams[satID] = cancel
	streamMu.Unlock()

	completed := streamBytesCancellable(ctx, satID, finalBytes)

	streamMu.Lock()
	delete(activeStreams, satID)
	streamMu.Unlock()

	// 🟢 FIX: Trigger notification with original inputPath to maintain string matching in Python
	if completed {
		SendPlaybackEnd(satID, inputPath)
	}

	return nil
}

// PlayPCM plays raw bytes directly
func PlayPCM(satID int, data []byte) error {
	streamMu.Lock()
	if cancel, exists := activeStreams[satID]; exists {
		cancel()
	}
	ctx, cancel := context.WithCancel(context.Background())
	activeStreams[satID] = cancel
	streamMu.Unlock()

	completed := streamBytesCancellable(ctx, satID, data)

	streamMu.Lock()
	delete(activeStreams, satID)
	streamMu.Unlock()

	if completed {
		SendPlaybackEnd(satID, "PCM_STREAM")
	}
	return nil
}

// streamBytesCancellable: Steady-State UDP Sync
func streamBytesCancellable(ctx context.Context, satID int, data []byte) bool {
	if GlobalSatServer == nil {
		return false
	}

	GlobalSatServer.mu.RLock()
	targetAddr, exists := GlobalSatServer.activeSatellites[satID]
	listener := GlobalSatServer.audioListener
	GlobalSatServer.mu.RUnlock()

	if !exists || targetAddr == nil || listener == nil {
		log.Printf("[DOWNLINK] ❌ Cannot stream: SatID %d unknown.", satID)
		return false
	}

	chunkSize := 1024
	padding := make([]byte, chunkSize)

	log.Printf("[DOWNLINK] 🚀 Streaming %d bytes (Golden 31.2ms Pace)...", len(data))

	// 🟢 STEP 1: ROBUST WAKE-UP
	for k := 0; k < 15; k++ {
		if listener != nil {
			listener.WriteToUDP(padding, targetAddr)
		}
		time.Sleep(20 * time.Millisecond)
	}
	time.Sleep(50 * time.Millisecond)

	// 🟢 STEP 2: PRECISE STEADY STREAMING
	pace := 31200 * time.Microsecond
	ticker := time.NewTicker(pace)
	defer ticker.Stop()

	ptr := 0
	for ptr < len(data) {
		select {
		case <-ctx.Done():
			log.Printf("[DOWNLINK] 🛑 Stream cancelled for Sat %d", satID)
			return false
		case <-ticker.C:
			end := ptr + chunkSize
			var chunk []byte
			if end > len(data) {
				lastChunk := data[ptr:]
				copy(padding, lastChunk)
				for k := len(lastChunk); k < chunkSize; k++ {
					padding[k] = 0
				}
				chunk = padding
			} else {
				chunk = data[ptr:end]
			}

			if listener != nil {
				listener.WriteToUDP(chunk, targetAddr)
			}
			ptr += chunkSize
		}
	}
	return true
}

func SendPlaybackEnd(satID int, filename string) {
	log.Printf("[DOWNLINK] ✅ Playback Finished for Sat %d: %s", satID, filename)
	if OnPlaybackFinished != nil {
		OnPlaybackFinished(satID, filename)
	}
}

// --- HELPERS ---

func StereoToMono(input []int16) []int16 {
	output := make([]int16, len(input)/2)
	for i := 0; i < len(output); i++ {
		output[i] = int16((int32(input[i*2]) + int32(input[i*2+1])) / 2)
	}
	return output
}

func Resample(input []int16, oldRate, newRate int) []int16 {
	if oldRate == newRate {
		return input
	}
	ratio := float64(oldRate) / float64(newRate)
	newLength := int(float64(len(input)) / ratio)
	output := make([]int16, newLength)
	for i := 0; i < len(output); i++ {
		srcIdx := int(float64(i) * ratio)
		if srcIdx < len(input) {
			output[i] = input[srcIdx]
		}
	}
	return output
}

func BytesToInt16(data []byte) []int16 {
	if len(data)%2 != 0 {
		data = data[:len(data)-1]
	}
	out := make([]int16, len(data)/2)
	for i := 0; i < len(out); i++ {
		out[i] = int16(binary.LittleEndian.Uint16(data[i*2:]))
	}
	return out
}

func Int16ToBytes(data []int16) []byte {
	buf := make([]byte, len(data)*2)
	for i, sample := range data {
		binary.LittleEndian.PutUint16(buf[i*2:], uint16(sample))
	}
	return buf
}
