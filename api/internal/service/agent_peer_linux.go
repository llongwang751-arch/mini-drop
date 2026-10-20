//go:build linux

package service

import (
	"errors"
	"net"
	"os"
	"path/filepath"
	"syscall"
	"time"

	"golang.org/x/sys/unix"
)

func verifyAgentPeer(conn net.Conn, expected uint32) error {
	unixConn, ok := conn.(*net.UnixConn)
	if !ok {
		return errors.New("AgentScope requires Unix peer credentials")
	}
	raw, err := unixConn.SyscallConn()
	if err != nil {
		return err
	}
	var peer *unix.Ucred
	var peerErr error
	if err := raw.Control(func(fd uintptr) { peer, peerErr = unix.GetsockoptUcred(int(fd), unix.SOL_SOCKET, unix.SO_PEERCRED) }); err != nil {
		return err
	}
	if peerErr != nil || peer == nil || peer.Uid != expected {
		return errors.New("AgentScope peer UID rejected")
	}
	return nil
}

type agentPeerListener struct{ net.Listener }

func (l agentPeerListener) Accept() (net.Conn, error) {
	for {
		conn, err := l.Listener.Accept()
		if err != nil {
			return nil, err
		}
		if verifyAgentPeer(conn, 10002) == nil {
			return conn, nil
		}
		conn.Close()
	}
}

func listenAgentGateway(socket string) (net.Listener, error) {
	if os.Getuid() != 10001 || !validAgentDirectory(socket, 10001) {
		return nil, errors.New("AgentScope gateway requires UID 10001 and private socket directory")
	}
	if info, err := os.Lstat(socket); err == nil {
		stat, ok := info.Sys().(*syscall.Stat_t)
		if info.Mode()&os.ModeSocket == 0 || !ok || stat.Uid != 10001 {
			return nil, errors.New("unsafe existing AgentScope socket")
		}
		conn, dialErr := net.DialTimeout("unix", socket, 250*time.Millisecond)
		if dialErr == nil {
			conn.Close()
			return nil, errors.New("AgentScope gateway already running")
		}
		if !errors.Is(dialErr, syscall.ECONNREFUSED) {
			return nil, dialErr
		}
		if err := os.Remove(socket); err != nil {
			return nil, err
		}
	} else if !os.IsNotExist(err) {
		return nil, err
	}
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: socket, Net: "unix"})
	if err != nil {
		return nil, err
	}
	listener.SetUnlinkOnClose(true)
	if err := os.Chmod(socket, 0660); err != nil {
		listener.Close()
		return nil, err
	}
	return agentPeerListener{listener}, nil
}

func validAgentDirectory(socket string, owner uint32) bool {
	if !cleanAgentSocket(socket) {
		return false
	}
	info, err := os.Lstat(filepath.Dir(socket))
	if err != nil {
		return false
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	return ok && stat.Uid == owner && stat.Gid == 10003 && info.Mode()&os.ModeSetgid != 0
}
