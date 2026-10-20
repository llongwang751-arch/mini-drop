//go:build linux

package service

import (
	"net"
	"os"
	"path/filepath"
	"testing"
)

func TestAgentUnixPeerCredentialsRejectDifferentUID(t *testing.T) {
	// A real local Unix connection, with imported protocol context only.
	socket := filepath.Join(t.TempDir(), "imported-peer.sock")
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: socket, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	accepted := make(chan *net.UnixConn, 1)
	go func() {
		conn, err := listener.AcceptUnix()
		if err == nil {
			accepted <- conn
		}
	}()
	client, err := net.DialUnix("unix", nil, &net.UnixAddr{Name: socket, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	server := <-accepted
	defer server.Close()
	uid := uint32(os.Getuid())
	if err := verifyAgentPeer(client, uid); err != nil {
		t.Fatal("actual Unix peer was rejected", err)
	}
	if err := verifyAgentPeer(server, uid+1); err == nil {
		t.Fatal("different UID admitted")
	}
	if err := verifyAgentPeer(nil, uid); err == nil {
		t.Fatal("non-Unix connection admitted")
	}
}

func TestAgentGatewayDirectoryRequiresOwnersGroupAndSetgid(t *testing.T) {
	directory := t.TempDir()
	socket := filepath.Join(directory, "imported.sock")
	if err := os.Chmod(directory, 0777); err != nil {
		t.Fatal(err)
	}
	if validAgentDirectory(socket, uint32(os.Getuid())) {
		t.Fatal("consumer-writable socket directory admitted")
	}
	if err := os.Chmod(directory, 0750); err != nil {
		t.Fatal(err)
	}
	if validAgentDirectory(socket, uint32(os.Getuid())) {
		t.Fatal("socket directory without setgid admitted")
	}
}
