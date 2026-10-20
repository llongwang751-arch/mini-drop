package main

import (
	"math"
	"testing"
	"time"
)

func TestNearestRankPercentilesAndThroughput(t *testing.T) {
	samples := make([]sample, 0, 101)
	for n := 100; n >= 1; n-- {
		samples = append(samples, sample{latency: time.Duration(n) * time.Millisecond})
	}
	samples = append(samples, sample{latency: 999 * time.Millisecond, code: "DeadlineExceeded"})
	got := summarize(samples, 2*time.Second)
	if got.Success != 100 || got.Failed != 1 || got.Attempted != 101 || got.SuccessPerSecond != 50 ||
		got.Latency.P50 == nil || *got.Latency.P50 != 50 || *got.Latency.P95 != 95 || *got.Latency.P99 != 99 ||
		got.FailureCodes["DeadlineExceeded"] != 1 {
		t.Fatalf("statistics changed: %+v", got)
	}
}

func TestNoSuccessDoesNotInventZeroLatency(t *testing.T) {
	got := summarize([]sample{{code: "Unavailable"}}, time.Second)
	if got.Latency.P50 != nil || got.Latency.P95 != nil || got.Latency.P99 != nil || got.SuccessPerSecond != 0 {
		t.Fatalf("failed calls invented success latency: %+v", got)
	}
	if summarize(nil, 0).SuccessPerSecond != 0 {
		t.Fatal("empty elapsed window did not remain finite")
	}
}

func TestSingleSuccessAndQueueDelay(t *testing.T) {
	got := summarize([]sample{{latency: 2500 * time.Microsecond, queued: 7 * time.Millisecond}}, time.Second)
	if *got.Latency.P50 != 2.5 || *got.Latency.P99 != 2.5 || *got.QueueDelay.P95 != 7 || math.IsNaN(got.SuccessPerSecond) {
		t.Fatalf("single-sample nearest rank or queue time changed: %+v", got)
	}
}
