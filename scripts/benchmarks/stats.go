// 统计受控 Core 心跳基准的成功延迟、调度排队延迟和吞吐，失败按固定错误码单独计数。
// 这些样本来自合成负载，不能当作业务请求延迟；没有成功样本时百分位应为空，而不是捏造零延迟。
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

// summarize 只对成功 RPC 计算延迟分位，失败次数和错误码单独保留。
// 吞吐使用完整测量窗口，不能只取成功调用耗时之和，也不能把失败样本当作零延迟成功。
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
