// 这是用于采集验收的可控 Go 合成负载，不是业务服务或生产性能证据。
// 标准库 pprof 仅监听回环地址；持续计算和有界保留内存让 CPU、分配与堆采样具备稳定可观察目标。
package main

import (
	"log"
	"math"
	"net/http"
	_ "net/http/pprof"
	"time"
)

var retained [][]byte
var sink float64

// goFixtureHotspot 保留独立热点函数，便于核验采集栈能否定位到预期符号；不得移除下面的编译器指令。
//go:noinline
func goFixtureHotspot(seed float64) float64 {
	for i := 0; i < 200000; i++ {
		seed = math.Sin(seed) + math.Sqrt(seed*seed+1.01)
	}
	return seed
}

func main() {
	go func() { log.Fatal(http.ListenAndServe("127.0.0.1:6060", nil)) }()
	for {
		sink = goFixtureHotspot(sink)
		retained = append(retained, make([]byte, 65536))
		if len(retained) > 256 {
			retained = retained[128:]
		}
		time.Sleep(time.Millisecond)
	}
}
