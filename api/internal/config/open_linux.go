package config

import (
	"os"
	"syscall"
)

func openRegular(name string) (*os.File, error) {
	// NONBLOCK prevents a swapped FIFO from blocking before the fstat check.
	// Secret projection volumes legitimately use symlinks. Follow their selected
	// target, then boundedFile validates the opened descriptor as a regular file.
	fd, err := syscall.Open(name, syscall.O_RDONLY|syscall.O_NONBLOCK|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, ErrInvalid
	}
	return os.NewFile(uintptr(fd), "configuration"), nil
}
