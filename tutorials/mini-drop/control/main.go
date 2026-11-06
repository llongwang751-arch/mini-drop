// A local, single-process teaching control plane. No production service imports.
package main

import (
	"context"
	"flag"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"
)

func main() {
	listen := flag.String("listen", "127.0.0.1:4330", "numeric loopback address only")
	collector := flag.String("collector", "", "absolute controlled collector executable")
	python := flag.String("python", "", "absolute Python 3.11+ executable")
	analyzer := flag.String("analyzer", "", "absolute path to this course analyzer.py")
	workdir := flag.String("workdir", "", "absolute private LOCAL learning directory")
	flag.Parse()
	if !loopbackHost(*listen) {
		log.Fatal("mini-drop: listen must be a numeric loopback address and port")
	}
	for _, path := range []string{*collector, *python, *analyzer} {
		info, err := os.Stat(path)
		if !filepath.IsAbs(path) || err != nil || !info.Mode().IsRegular() {
			log.Fatal("mini-drop: valid absolute program paths required")
		}
	}
	s, err := newStore(*workdir)
	if err != nil {
		log.Fatal("mini-drop: journal could not be opened")
	}
	if err = s.recover(); err != nil {
		log.Fatal("mini-drop: journal could not be recovered safely")
	}
	listener, err := net.Listen("tcp", *listen)
	if err != nil {
		log.Fatal("mini-drop: local port unavailable")
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	go worker(ctx, s, programConfig{*collector, *python, *analyzer})
	server := &http.Server{Handler: &api{s}, ReadHeaderTimeout: 3 * time.Second, ReadTimeout: 5 * time.Second, WriteTimeout: 10 * time.Second, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 8192}
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdown)
	}()
	log.Printf("mini-drop: local teaching API ready at http://%s", *listen)
	if err = server.Serve(listener); err != nil && err != http.ErrServerClosed {
		log.Print("mini-drop: HTTP listener stopped")
	}
}
