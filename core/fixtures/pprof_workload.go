// 这是受控运行的 Go 合成采集目标，通过标准库真实 pprof 处理器暴露回环接口。
// 两个工作协程有意在锁内执行计算与分配，形成 CPU、堆、mutex 和 block 的可观察线索；不能把夹具表现外推到生产。
package main

import (
	"context"
	"log"
	"math"
	"net/http"
	_ "net/http/pprof"
	"os"
	"os/signal"
	"runtime"
	"sync"
	"syscall"
	"time"
)

var sink float64
var retained [][]byte
var lock sync.Mutex

func transform(seed int) float64 {
	var value float64
	for i := 1; i < 40000; i++ {
		value += math.Sqrt(float64((i + seed) % 10007))
	}
	return value
}

// allocate 周期性分配并限制保留对象数量，为堆与分配采样提供合成信号，不代表业务内存泄漏。
func allocate(seed int) {
	retained = append(retained, make([]byte, 16384+seed%2048))
	if len(retained) > 256 {
		retained = retained[128:]
	}
}

func main() {
	runtime.SetMutexProfileFraction(1)
	runtime.SetBlockProfileRate(1)
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	server := &http.Server{Addr: "127.0.0.1:6060", ReadHeaderTimeout: 5 * time.Second}
	go func() {
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatal(err)
		}
	}()
	for worker := 0; worker < 2; worker++ {
		go func() {
			for seed := 0; ; seed++ {
				select {
				case <-ctx.Done():
					return
				default:
				}
				lock.Lock()
				sink = transform(seed)
				allocate(seed)
				lock.Unlock()
				time.Sleep(time.Millisecond * 5)
			}
		}()
	}
	<-ctx.Done()
	shutdown, cancel := context.WithTimeout(context.Background(), time.Second*3)
	defer cancel()
	_ = server.Shutdown(shutdown)
}
