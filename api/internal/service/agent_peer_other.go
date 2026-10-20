//go:build !linux

package service

import (
	"errors"
	"net"
)

func verifyAgentPeer(net.Conn, uint32) error {
	return errors.New("AgentScope peer credentials require Linux")
}
func listenAgentGateway(string) (net.Listener, error) {
	return nil, errors.New("AgentScope gateway requires Linux")
}

func validAgentDirectory(string, uint32) bool { return false }
