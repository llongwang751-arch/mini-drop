package main

import (
	"crypto/sha256"
	"fmt"
	"log"
	"net/http"
	_ "net/http/pprof"
	"os"
	"runtime"
	"time"
)

func hotLoop() {
	payload := []byte("mini-drop-go-hotspot")
	for {
		sum := sha256.Sum256(payload)
		payload = append(sum[:], payload[:8]...)
		if len(payload) > 4096 {
			payload = payload[:32]
		}
		runtime.Gosched()
	}
}

func main() {
	go hotLoop()
	http.HandleFunc("/health", func(writer http.ResponseWriter, _ *http.Request) {
		_, _ = fmt.Fprintln(writer, "ok")
	})
	address := ":6060"
	if value := os.Getenv("LISTEN_ADDR"); value != "" {
		address = value
	}
	log.Printf("Go hotspot and pprof listening on %s", address)
	server := &http.Server{
		Addr:              address,
		ReadHeaderTimeout: 3 * time.Second,
	}
	log.Fatal(server.ListenAndServe())
}

