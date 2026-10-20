//go:build !linux

// Package service：不支持 Linux SO_PEERCRED 的平台明确拒绝私有 AgentScope 运行，避免开发平台静默跳过身份校验。
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
