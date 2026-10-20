// 本文件验证Linux FIFO 等特殊文件不能阻塞配置读取，描述符类型校验应失败关闭。
// 测试名称标明具体场景；涉及真实数据库的用例需显式配置测试连接，协议夹具不代表生产或模型实测。
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
