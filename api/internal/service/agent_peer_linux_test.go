//go:build linux

// 本文件验证真实 Linux Unix 连接的 UID 识别，以及 socket 目录属主、组和 setgid 边界。
// 测试名称标明具体场景；涉及真实数据库的用例需显式配置测试连接，协议夹具不代表生产或模型实测。
package service

import (
	"net"
	"os"
	"path/filepath"
	"testing"
)

func TestAgentUnixPeerCredentialsRejectDifferentUID(t *testing.T) {
	// 使用真实本机 Unix 连接核验内核进程身份；请求上下文是明确导入的协议夹具。
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
