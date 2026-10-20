// Package config：Linux 专用的秘密文件打开边界，先打开描述符再判断类型，阻止 FIFO 与路径替换造成阻塞。
package config

import (
	"os"
	"syscall"
)

func openRegular(name string) (*os.File, error) {
	// NONBLOCK 防止路径被替换成 FIFO 后，进程在 fstat 校验之前就阻塞。
	// 投射秘密的卷可以合法使用符号链接；先打开其指向的目标，
	// 再由 boundedFile 对已打开的描述符校验普通文件类型和大小，避免检查后替换的竞态。
	fd, err := syscall.Open(name, syscall.O_RDONLY|syscall.O_NONBLOCK|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, ErrInvalid
	}
	return os.NewFile(uintptr(fd), "configuration"), nil
}
