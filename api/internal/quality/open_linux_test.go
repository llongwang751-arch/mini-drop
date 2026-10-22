package quality

import (
	"os"
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

func TestFIFOAndSymlinkDoNotEscapeOrBlock(t *testing.T) {
	dir := t.TempDir()
	name := filepath.Join(dir, "fixture-run-001.json")
	if syscall.Mkfifo(name, 0600) != nil {
		t.Fatal("fifo fixture failed")
	}
	done := make(chan bool, 1)
	go func() { _, err := Get(dir, "fixture-run-001"); done <- err != nil }()
	select {
	case ok := <-done:
		if !ok {
			t.Fatal("FIFO accepted")
		}
	case <-time.After(time.Second):
		t.Fatal("FIFO blocked reader")
	}
	if err := os.Remove(name); err != nil {
		t.Fatal(err)
	}
	outside := filepath.Join(t.TempDir(), "outside.json")
	if os.WriteFile(outside, []byte(`{}`), 0600) != nil {
		t.Fatal("outside fixture")
	}
	if os.Symlink(outside, name) != nil {
		t.Fatal("symlink fixture")
	}
	if _, err := Get(dir, "fixture-run-001"); err == nil {
		t.Fatal("root escape accepted")
	}
	if _, err := List(dir); err == nil {
		t.Fatal("symlink catalog accepted")
	}
}
