//go:build !linux

package config

import "os"

func openRegular(name string) (*os.File, error) {
	info, err := os.Stat(name)
	if err != nil || !info.Mode().IsRegular() {
		return nil, ErrInvalid
	}
	return os.Open(name)
}
