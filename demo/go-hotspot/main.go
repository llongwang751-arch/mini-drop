package main

import (
	"crypto/sha256"
	"encoding/json"
	"io"
	"net/http"
	_ "net/http/pprof"
	"os"
	"runtime"
	"runtime/debug"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// faultSwitch is deliberately tiny and bounded. The demo never starts a
// fault at boot, and every start request receives an independent deadline.
type faultSwitch struct {
	mu       sync.Mutex
	active   bool
	deadline time.Time
}

func (fault *faultSwitch) start(seconds int) {
	if seconds < 15 {
		seconds = 15
	}
	if seconds > 300 {
		seconds = 300
	}
	fault.mu.Lock()
	fault.active = true
	fault.deadline = time.Now().Add(time.Duration(seconds) * time.Second)
	fault.mu.Unlock()
}

func (fault *faultSwitch) stop() {
	fault.mu.Lock()
	fault.active = false
	fault.deadline = time.Time{}
	fault.mu.Unlock()
}

func (fault *faultSwitch) snapshot() (bool, float64) {
	fault.mu.Lock()
	defer fault.mu.Unlock()
	if fault.active && !fault.deadline.IsZero() && !time.Now().Before(fault.deadline) {
		fault.active = false
		fault.deadline = time.Time{}
	}
	remaining := 0.0
	if fault.active {
		remaining = max(0, time.Until(fault.deadline).Seconds())
	}
	return fault.active, remaining
}

var cpuFault faultSwitch
var networkFault faultSwitch
var memoryFault faultSwitch
var ioFault faultSwitch
var cpuOperations atomic.Uint64
var networkRequests atomic.Uint64
var networkFailures atomic.Uint64
var networkLatencyNanos atomic.Uint64
var networkDelayMillis atomic.Int64
var memoryTargetBytes atomic.Int64
var retainedMemoryBytes atomic.Int64
var retainedMemoryMu sync.Mutex
var retainedMemory [][]byte
var ioBytesWritten atomic.Uint64
var ioOperations atomic.Uint64
var ioFailures atomic.Uint64
var ioWorkMu sync.Mutex

const (
	goIOPath          = "/tmp/mini-drop-go-io-fault.bin"
	appMetricsPath    = "/tmp/mini-drop-app-metrics.json"
	goIOMaxFileBytes  = 64 * 1024 * 1024
	goMemoryChunkSize = 2 * 1024 * 1024
)

func hostPID() int {
	data, err := os.ReadFile("/proc/self/status")
	if err == nil {
		for _, line := range strings.Split(string(data), "\n") {
			if strings.HasPrefix(line, "NSpid:") {
				fields := strings.Fields(strings.TrimPrefix(line, "NSpid:"))
				if len(fields) > 0 {
					if value, parseErr := strconv.Atoi(fields[0]); parseErr == nil {
						return value
					}
				}
			}
		}
	}
	return os.Getpid()
}

func writeJSON(writer http.ResponseWriter, status int, payload map[string]any) {
	writer.Header().Set("Content-Type", "application/json; charset=utf-8")
	writer.WriteHeader(status)
	_ = json.NewEncoder(writer).Encode(payload)
}

func requestPayload(request *http.Request) map[string]any {
	defer request.Body.Close()
	data, err := io.ReadAll(io.LimitReader(request.Body, 4096))
	if err != nil || len(data) == 0 {
		return map[string]any{}
	}
	var payload map[string]any
	if json.Unmarshal(data, &payload) != nil {
		return map[string]any{}
	}
	return payload
}

func intOption(payload map[string]any, key string, fallback int) int {
	value, ok := payload[key]
	if !ok {
		return fallback
	}
	switch item := value.(type) {
	case float64:
		return int(item)
	case string:
		parsed, err := strconv.Atoi(item)
		if err == nil {
			return parsed
		}
	}
	return fallback
}

func snapshot() map[string]any {
	cpuActive, cpuRemaining := cpuFault.snapshot()
	networkActive, networkRemaining := networkFault.snapshot()
	memoryActive, memoryRemaining := memoryFault.snapshot()
	ioActive, ioRemaining := ioFault.snapshot()
	requests := networkRequests.Load()
	var memoryStats runtime.MemStats
	runtime.ReadMemStats(&memoryStats)
	averageLatency := 0.0
	if requests > 0 {
		averageLatency = float64(networkLatencyNanos.Load()) / float64(requests) / float64(time.Millisecond)
	}
	return map[string]any{
		"status": "ok", "runtime": "go", "pid": os.Getpid(), "host_pid": hostPID(),
		"fault_active": cpuActive, "cpu_fault_active": cpuActive,
		"cpu_operations":                      cpuOperations.Load(),
		"cpu_auto_stop_remaining_seconds":     cpuRemaining,
		"network_fault_active":                networkActive,
		"network_delay_ms":                    networkDelayMillis.Load(),
		"network_requests":                    requests,
		"network_failures":                    networkFailures.Load(),
		"network_average_latency_ms":          averageLatency,
		"network_auto_stop_remaining_seconds": networkRemaining,
		"memory_fault_active":                 memoryActive,
		"retained_memory_bytes":               retainedMemoryBytes.Load(),
		"heap_alloc_bytes":                    memoryStats.HeapAlloc,
		"memory_auto_stop_remaining_seconds":  memoryRemaining,
		"io_fault_active":                     ioActive,
		"io_bytes_written":                    ioBytesWritten.Load(),
		"io_operations":                       ioOperations.Load(),
		"io_failures":                         ioFailures.Load(),
		"io_auto_stop_remaining_seconds":      ioRemaining,
	}
}

func applicationMetricsSnapshot() map[string]any {
	source := snapshot()
	metrics := map[string]any{
		"schema_version":      "mini-drop.application-metrics.v1",
		"captured_at_unix_ms": time.Now().UnixMilli(),
	}
	for _, key := range []string{
		"runtime", "pid", "host_pid", "cpu_operations",
		"network_delay_ms", "network_requests", "network_failures",
		"network_average_latency_ms", "retained_memory_bytes",
		"heap_alloc_bytes", "io_bytes_written", "io_operations", "io_failures",
	} {
		if value, ok := source[key]; ok {
			metrics[key] = value
		}
	}
	return metrics
}

func publishApplicationMetrics() {
	ticker := time.NewTicker(200 * time.Millisecond)
	defer ticker.Stop()
	for {
		payload, err := json.Marshal(applicationMetricsSnapshot())
		if err == nil {
			temporary := appMetricsPath + ".tmp"
			if err = os.WriteFile(temporary, payload, 0o600); err == nil {
				_ = os.Rename(temporary, appMetricsPath)
			}
		}
		<-ticker.C
	}
}

// goCPUHotFunction has a stable symbol so pprof can point at source-level work.
//
//go:noinline
func goCPUHotFunction(value []byte) []byte {
	for index := 0; index < 500; index++ {
		digest := sha256.Sum256(value)
		value = digest[:]
	}
	cpuOperations.Add(500)
	return value
}

func runCPUFault() {
	value := []byte("mini-drop-go-hotspot")
	for {
		active, _ := cpuFault.snapshot()
		if !active {
			time.Sleep(25 * time.Millisecond)
			continue
		}
		value = goCPUHotFunction(value)
	}
}

func releaseGoMemory() {
	retainedMemoryMu.Lock()
	retainedMemory = nil
	retainedMemoryBytes.Store(0)
	retainedMemoryMu.Unlock()
	runtime.GC()
	debug.FreeOSMemory()
}

func runMemoryFault() {
	wasActive := false
	for {
		active, _ := memoryFault.snapshot()
		if !active {
			if wasActive {
				releaseGoMemory()
				wasActive = false
			}
			time.Sleep(50 * time.Millisecond)
			continue
		}
		wasActive = true
		if retainedMemoryBytes.Load() < memoryTargetBytes.Load() {
			chunk := make([]byte, goMemoryChunkSize)
			for offset := 0; offset < len(chunk); offset += 4096 {
				chunk[offset] = 1
			}
			retainedMemoryMu.Lock()
			stillActive, _ := memoryFault.snapshot()
			if stillActive && retainedMemoryBytes.Load() < memoryTargetBytes.Load() {
				retainedMemory = append(retainedMemory, chunk)
				retainedMemoryBytes.Add(int64(len(chunk)))
			}
			retainedMemoryMu.Unlock()
		}
		time.Sleep(180 * time.Millisecond)
	}
}

func cleanupGoIO() {
	ioWorkMu.Lock()
	defer ioWorkMu.Unlock()
	_ = os.Remove(goIOPath)
}

func runIOFault() {
	chunk := make([]byte, 128*1024)
	copy(chunk, []byte("mini-drop-go-synchronous-io"))
	wasActive := false
	for {
		active, _ := ioFault.snapshot()
		if !active {
			if wasActive {
				cleanupGoIO()
				wasActive = false
			}
			time.Sleep(40 * time.Millisecond)
			continue
		}
		wasActive = true
		ioWorkMu.Lock()
		stillActive, _ := ioFault.snapshot()
		if !stillActive {
			ioWorkMu.Unlock()
			continue
		}
		file, err := os.OpenFile(goIOPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
		if err == nil {
			var count int
			count, err = file.Write(chunk)
			if err == nil {
				err = file.Sync()
			}
			_ = file.Close()
			if err == nil {
				ioBytesWritten.Add(uint64(count))
				ioOperations.Add(1)
			}
		}
		if err != nil {
			ioFailures.Add(1)
			ioFault.stop()
		}
		if info, statErr := os.Stat(goIOPath); statErr == nil && info.Size() >= goIOMaxFileBytes {
			_ = os.Remove(goIOPath)
		}
		ioWorkMu.Unlock()
		time.Sleep(20 * time.Millisecond)
	}
}

func runNetworkClient() {
	client := &http.Client{Timeout: 2 * time.Second}
	for {
		active, _ := networkFault.snapshot()
		if !active {
			time.Sleep(50 * time.Millisecond)
			continue
		}
		started := time.Now()
		response, err := client.Get("http://127.0.0.1:6060/work")
		latency := time.Since(started)
		networkRequests.Add(1)
		networkLatencyNanos.Add(uint64(latency))
		if err != nil {
			networkFailures.Add(1)
		} else {
			_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 1024))
			response.Body.Close()
		}
		time.Sleep(20 * time.Millisecond)
	}
}

func requirePOST(writer http.ResponseWriter, request *http.Request) bool {
	if request.Method == http.MethodPost {
		return true
	}
	writeJSON(writer, http.StatusMethodNotAllowed, map[string]any{"error": "method_not_allowed"})
	return false
}

func main() {
	networkDelayMillis.Store(240)
	memoryTargetBytes.Store(96 * 1024 * 1024)
	go runCPUFault()
	go runMemoryFault()
	go runIOFault()
	go publishApplicationMetrics()
	for index := 0; index < 4; index++ {
		go runNetworkClient()
	}

	http.HandleFunc("/health", func(writer http.ResponseWriter, _ *http.Request) {
		writeJSON(writer, http.StatusOK, snapshot())
	})
	http.HandleFunc("/snapshot", func(writer http.ResponseWriter, _ *http.Request) {
		writeJSON(writer, http.StatusOK, snapshot())
	})
	http.HandleFunc("/work", func(writer http.ResponseWriter, _ *http.Request) {
		active, _ := networkFault.snapshot()
		if active {
			time.Sleep(time.Duration(networkDelayMillis.Load()) * time.Millisecond)
		}
		writeJSON(writer, http.StatusOK, map[string]any{"status": "ok", "delayed": active})
	})
	http.HandleFunc("/faults/cpu/start", func(writer http.ResponseWriter, request *http.Request) {
		if !requirePOST(writer, request) {
			return
		}
		payload := requestPayload(request)
		cpuFault.start(intOption(payload, "duration_seconds", 60))
		writeJSON(writer, http.StatusOK, snapshot())
	})
	http.HandleFunc("/faults/cpu/stop", func(writer http.ResponseWriter, request *http.Request) {
		if !requirePOST(writer, request) {
			return
		}
		cpuFault.stop()
		writeJSON(writer, http.StatusOK, snapshot())
	})
	http.HandleFunc("/faults/network/start", func(writer http.ResponseWriter, request *http.Request) {
		if !requirePOST(writer, request) {
			return
		}
		payload := requestPayload(request)
		delay := intOption(payload, "delay_ms", 240)
		if delay < 50 {
			delay = 50
		}
		if delay > 1000 {
			delay = 1000
		}
		networkRequests.Store(0)
		networkFailures.Store(0)
		networkLatencyNanos.Store(0)
		networkDelayMillis.Store(int64(delay))
		networkFault.start(intOption(payload, "duration_seconds", 60))
		writeJSON(writer, http.StatusOK, snapshot())
	})
	http.HandleFunc("/faults/network/stop", func(writer http.ResponseWriter, request *http.Request) {
		if !requirePOST(writer, request) {
			return
		}
		networkFault.stop()
		writeJSON(writer, http.StatusOK, snapshot())
	})
	http.HandleFunc("/faults/memory/start", func(writer http.ResponseWriter, request *http.Request) {
		if !requirePOST(writer, request) {
			return
		}
		payload := requestPayload(request)
		megabytes := intOption(payload, "megabytes", 96)
		if megabytes < 16 {
			megabytes = 16
		}
		if megabytes > 128 {
			megabytes = 128
		}
		memoryFault.stop()
		releaseGoMemory()
		memoryTargetBytes.Store(int64(megabytes * 1024 * 1024))
		memoryFault.start(intOption(payload, "duration_seconds", 60))
		writeJSON(writer, http.StatusOK, snapshot())
	})
	http.HandleFunc("/faults/memory/stop", func(writer http.ResponseWriter, request *http.Request) {
		if !requirePOST(writer, request) {
			return
		}
		memoryFault.stop()
		releaseGoMemory()
		writeJSON(writer, http.StatusOK, snapshot())
	})
	http.HandleFunc("/faults/io/start", func(writer http.ResponseWriter, request *http.Request) {
		if !requirePOST(writer, request) {
			return
		}
		payload := requestPayload(request)
		ioFault.stop()
		cleanupGoIO()
		ioBytesWritten.Store(0)
		ioOperations.Store(0)
		ioFailures.Store(0)
		ioFault.start(intOption(payload, "duration_seconds", 60))
		writeJSON(writer, http.StatusOK, snapshot())
	})
	http.HandleFunc("/faults/io/stop", func(writer http.ResponseWriter, request *http.Request) {
		if !requirePOST(writer, request) {
			return
		}
		ioFault.stop()
		cleanupGoIO()
		writeJSON(writer, http.StatusOK, snapshot())
	})
	if err := http.ListenAndServe(":6060", nil); err != nil {
		panic(err)
	}
}
