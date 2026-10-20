//go:build !linux

// Package config：非 Linux 平台的文件打开适配，供开发与配置测试使用；平台差异不改变上层的大小、类型和脱敏约束。
package config

import "os"

func openRegular(name string) (*os.File, error) {
	info, err := os.Stat(name)
	if err != nil || !info.Mode().IsRegular() {
		return nil, ErrInvalid
	}
	return os.Open(name)
}
