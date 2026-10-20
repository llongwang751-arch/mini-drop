package config

import (
	"errors"
	"os"
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

func TestLinuxSpecialFilesCannotBlockConfigurationLoading(t *testing.T) {
	directory := t.TempDir()
	fifo := filepath.Join(directory, "imported-fifo")
	if err := syscall.Mkfifo(fifo, 0600); err != nil {
		t.Fatal(err)
	}
	finished := make(chan error, 1)
	go func() { _, err := Load([]string{"--config", fifo}, environmentFixture(nil)); finished <- err }()
	select {
	case err := <-finished:
		if !errors.Is(err, ErrInvalid) {
			t.Fatal("FIFO configuration was accepted")
		}
	case <-time.After(time.Second):
		t.Fatal("FIFO configuration blocked instead of rejecting")
	}
	file := fixtureFile(t, `{}`)
	link := filepath.Join(directory, "imported-link")
	if os.Symlink(file, link) != nil {
		t.Fatal("Could not construct imported symlink fixture")
	}
	if _, err := Load([]string{"--config", link}, environmentFixture(nil)); err != nil {
		t.Fatal("A regular projected configuration file must remain supported")
	}
}
