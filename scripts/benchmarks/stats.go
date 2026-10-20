package main

import (
	"math"
	"sort"
	"time"
)

type sample struct {
	latency time.Duration
	queued  time.Duration
	code    string
}

type percentiles struct {
	P50 *float64 `json:"p50_ms"`
	P95 *float64 `json:"p95_ms"`
	P99 *float64 `json:"p99_ms"`
}

type statistics struct {
	Attempted        int            `json:"attempted"`
	Success          int            `json:"success"`
	Failed           int            `json:"failed"`
	ElapsedSeconds   float64        `json:"elapsed_seconds"`
	SuccessPerSecond float64        `json:"successful_heartbeats_per_second"`
	Latency          percentiles    `json:"successful_rpc_latency"`
	QueueDelay       percentiles    `json:"successful_scheduler_queue_delay"`
	FailureCodes     map[string]int `json:"failure_codes"`
}

func ranks(values []float64) percentiles {
	if len(values) == 0 {
		return percentiles{}
	}
	sort.Float64s(values)
	value := func(q float64) *float64 {
		result := values[int(math.Ceil(q*float64(len(values))))-1]
		return &result
	}
	return percentiles{value(.50), value(.95), value(.99)}
}

func summarize(samples []sample, elapsed time.Duration) statistics {
	result := statistics{Attempted: len(samples), ElapsedSeconds: elapsed.Seconds(), FailureCodes: map[string]int{}}
	var latency, queued []float64
	for _, item := range samples {
		if item.code != "" {
			result.Failed++
			result.FailureCodes[item.code]++
			continue
		}
		result.Success++
		latency = append(latency, float64(item.latency)/float64(time.Millisecond))
		queued = append(queued, float64(item.queued)/float64(time.Millisecond))
	}
	result.Latency, result.QueueDelay = ranks(latency), ranks(queued)
	if elapsed > 0 {
		result.SuccessPerSecond = float64(result.Success) / elapsed.Seconds()
	}
	return result
}
