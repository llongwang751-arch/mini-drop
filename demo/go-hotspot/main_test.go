package main

import (
	"sync"
	"testing"
	"time"
)

func TestIOTimingSnapshotsKeepCountsAndElapsedInTheSameWindow(t *testing.T) {
	ioMetricsMu.Lock()
	ioBytesWritten.Store(0)
	ioOperations.Store(0)
	ioDurationNanos.Store(0)
	ioMetricsMu.Unlock()
	var writers sync.WaitGroup
	for i := 0; i < 8; i++ {
		writers.Add(1)
		go func() {
			defer writers.Done()
			for j := 0; j < 50; j++ {
				recordSuccessfulIO(128, 15*time.Millisecond)
			}
		}()
	}
	done := make(chan struct{})
	go func() { writers.Wait(); close(done) }()
	for {
		metrics := applicationMetricsSnapshot()
		count := metrics["io_operations"].(uint64)
		duration := metrics["io_operation_duration_ms_total"].(float64)
		if duration != float64(count)*15 {
			t.Fatalf("mixed count/duration window: %d operations, %f ms", count, duration)
		}
		if _, leaksFault := metrics["io_fault_active"]; leaksFault {
			t.Fatal("fault oracle leaked into application measurements")
		}
		select {
		case <-done:
			if snapshot()["io_operations"].(uint64) != 400 {
				t.Fatal("lost successful I/O observations")
			}
			return
		default:
		}
	}
}
