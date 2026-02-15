package downlink

import (
	"context"
	"encoding/binary"
	"io/ioutil"
	"log"
	"net"
	"os"
	"sync"
	"time"
)

// --- GLOBAL SERVER INSTANCE ---
var GlobalSatServer *SatelliteServer

// SatelliteServer handles UDP audio streams and playback routing
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

// StartAudioServer listens for incoming UDP audio (Lane 1)
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

	buffer := make([]byte, 2048)
	pythonAddr, _ := net.ResolveUDPAddr("udp", "127.0.0.1:6000")

	for {
		n, remoteAddr, err := conn.ReadFromUDP(buffer)
		if err != nil {
			continue
		}

		// 🟢 FIX 1: THE SILENT HANDSHAKE
		// We process ANY packet (n >= 1) to learn the IP.
		// Even a 1-byte "Ping" allows us to register the route.
		if n >= 1 {
			satID := int(buffer[0])

			// 🧠 LEARN ROUTE: Map SatID to IP
			s.mu.Lock()
			// Log only on change to avoid spam
			if existing, ok := s.activeSatellites[satID]; !ok || existing.String() != remoteAddr.String() {
				log.Printf("[DOWNLINK] 📡 Learned Route: Sat %d -> %s", satID, remoteAddr.String())
			}
			s.activeSatellites[satID] = remoteAddr
			s.mu.Unlock()

			// 🚀 FORWARDING: Only forward ACTUAL AUDIO (payload > 0) to Python
			if n > 1 {
				conn.WriteToUDP(buffer[:n], pythonAddr)
			}
		}
	}
}

// StartControlServer Stub (Legacy Compatibility)
func (s *SatelliteServer) StartControlServer(address string) {
	log.Printf("[DOWNLINK] ⚠️ Control Server (TCP) is managed by Python directly. This is a stub.")
}

// =========================================================================
//  ⏯️ AUDIO PLAYER ENGINE (Multi-Node & Cancellable)
// =========================================================================

var (
	streamMu      sync.RWMutex
	activeStreams = make(map[int]context.CancelFunc)
)

// StopPlayback immediately kills the audio stream for a SPECIFIC satellite
func StopPlayback(satID int) {
	streamMu.Lock()
	defer streamMu.Unlock()

	if cancel, exists := activeStreams[satID]; exists {
		log.Printf("[DOWNLINK] 🛑 KILL SIGNAL RECEIVED for Sat %d. Stopping Audio.", satID)
		cancel()
		delete(activeStreams, satID)
	}
}

// PlayAudio loads a WAV, processes it, streams it, and deletes it.
func PlayAudio(satID int, filepath string) error {
	defer func() {
		log.Printf("[DOWNLINK] Cleaning up temp file: %s", filepath)
		os.Remove(filepath)
	}()

	file, err := os.Open(filepath)
	if err != nil {
		log.Printf("[DOWNLINK] Error opening file: %v", err)
		return err
	}

	raw, err := ioutil.ReadAll(file)
	file.Close()
	if err != nil {
		return err
	}

	// 1. Parse Header
	if len(raw) < 44 {
		return nil
	}
	numChannels := binary.LittleEndian.Uint16(raw[22:24])
	sampleRate := binary.LittleEndian.Uint32(raw[24:28])

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

	// 2. Audio Hygiene: Force 16k Mono
	if numChannels == 2 {
		inputSamples = StereoToMono(inputSamples)
	}
	if sampleRate != 16000 {
		inputSamples = Resample(inputSamples, int(sampleRate), 16000)
	}

	// 3. Volume & Byte Conversion
	finalBytes := ReduceVolumeToBytes(inputSamples, 0.3)

	// 4. Register Stream
	streamMu.Lock()
	if cancel, exists := activeStreams[satID]; exists {
		cancel()
	}
	ctx, cancel := context.WithCancel(context.Background())
	activeStreams[satID] = cancel
	streamMu.Unlock()

	// 5. Stream
	streamBytesCancellable(ctx, satID, finalBytes)

	// Cleanup
	streamMu.Lock()
	delete(activeStreams, satID)
	streamMu.Unlock()
	return nil
}

// PlayPCM plays raw bytes (Cancellable) - Used by filler/beeps
func PlayPCM(satID int, data []byte) error {
	// Assume data is already 16k Mono
	samples := BytesToInt16(data)
	quieterData := ReduceVolumeToBytes(samples, 0.3)

	streamMu.Lock()
	if cancel, exists := activeStreams[satID]; exists {
		cancel()
	}
	ctx, cancel := context.WithCancel(context.Background())
	activeStreams[satID] = cancel
	streamMu.Unlock()

	streamBytesCancellable(ctx, satID, quieterData)

	streamMu.Lock()
	delete(activeStreams, satID)
	streamMu.Unlock()
	return nil
}

// --- CORE STREAMING LOOP (TUNED PHYSICS) ---
func streamBytesCancellable(ctx context.Context, satID int, data []byte) bool {
	if GlobalSatServer == nil {
		return false
	}

	GlobalSatServer.mu.RLock()
	targetAddr, exists := GlobalSatServer.activeSatellites[satID]
	listener := GlobalSatServer.audioListener
	GlobalSatServer.mu.RUnlock()

	if !exists || targetAddr == nil || listener == nil {
		log.Printf("[DOWNLINK] ❌ Cannot stream: SatID %d unknown (No route learned yet).", satID)
		return false
	}

	// 🟢 PHYSICS MATCH: Tuned to your Python Script
	// 1024 bytes = 32ms of audio @ 16kHz
	chunkSize := 1024

	// Python Script: 30ms Sleep
	// This leaves ~2ms of headroom per packet to prevent buffer overflow
	sleepDuration := 30 * time.Millisecond

	log.Printf("[DOWNLINK] 🚀 Streaming %d bytes to %s...", len(data), targetAddr.String())

	for i := 0; i < len(data); i += chunkSize {
		// 1. Check Cancel
		select {
		case <-ctx.Done():
			log.Printf("[DOWNLINK] 🛑 Stream cancelled for Sat %d", satID)
			return false
		default:
		}

		// 2. Prepare Chunk
		end := i + chunkSize
		if end > len(data) {
			end = len(data)
		}
		chunk := data[i:end]

		// 🟢 PADDING FIX: Ensure every packet is exactly 1024 bytes
		// Some ESP32 DMA/I2S implementations glitch on variable sized packets
		if len(chunk) < chunkSize {
			padding := make([]byte, chunkSize-len(chunk))
			chunk = append(chunk, padding...)
		}

		// 3. Send
		if listener != nil {
			listener.WriteToUDP(chunk, targetAddr)
		}

		// 4. Steady Drip
		time.Sleep(sleepDuration)
	}
	return true
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

func ReduceVolumeToBytes(samples []int16, scale float32) []byte {
	buf := make([]byte, len(samples)*2)
	for i, sample := range samples {
		scaled := int16(float32(sample) * scale)
		binary.LittleEndian.PutUint16(buf[i*2:], uint16(scaled))
	}
	return buf
}

func Int16ToBytes(data []int16) []byte {
	buf := make([]byte, len(data)*2)
	for i, sample := range data {
		binary.LittleEndian.PutUint16(buf[i*2:], uint16(sample))
	}
	return buf
}
