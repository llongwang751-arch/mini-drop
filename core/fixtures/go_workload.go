// Instrumented toy: standard Go pprof on a loopback-only listener.
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
