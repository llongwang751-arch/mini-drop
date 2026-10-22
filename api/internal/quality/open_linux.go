package quality

import (
	"os"
	"syscall"
)

// 根目录句柄阻止路径越界；NONBLOCK防止先被FIFO阻塞、来不及检查描述符类型。
func openRecord(root *os.Root, name string) (*os.File, error) {
	return root.OpenFile(name, os.O_RDONLY|syscall.O_NONBLOCK, 0)
}
