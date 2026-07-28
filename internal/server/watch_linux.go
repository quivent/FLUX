//go:build linux

package server

import (
	"context"
	"os"
	"path/filepath"
	"syscall"
)

const inotifyMask = syscall.IN_CREATE | syscall.IN_DELETE | syscall.IN_DELETE_SELF |
	syscall.IN_MODIFY | syscall.IN_MOVE_SELF | syscall.IN_MOVED_FROM | syscall.IN_MOVED_TO |
	syscall.IN_ATTRIB | syscall.IN_CLOSE_WRITE

func waitForPathChange(ctx context.Context, target string) bool {
	return waitForLinuxChange(ctx, filepath.Dir(target), false)
}

func waitForTreeChange(ctx context.Context, root string) bool {
	return waitForLinuxChange(ctx, root, true)
}

func waitForLinuxChange(ctx context.Context, root string, recursive bool) bool {
	fd, err := syscall.InotifyInit1(syscall.IN_CLOEXEC)
	if err != nil {
		return false
	}
	defer syscall.Close(fd)
	add := func(dir string) {
		_, _ = syscall.InotifyAddWatch(fd, dir, inotifyMask)
	}
	if recursive {
		_ = filepath.WalkDir(root, func(name string, entry os.DirEntry, walkErr error) error {
			if walkErr == nil && entry.IsDir() {
				add(name)
			}
			return nil
		})
	} else {
		add(root)
	}
	done := make(chan struct{})
	go func() {
		select {
		case <-ctx.Done():
			_ = syscall.Close(fd)
		case <-done:
		}
	}()
	buffer := make([]byte, 16*1024)
	n, readErr := syscall.Read(fd, buffer)
	close(done)
	return readErr == nil && n >= syscall.SizeofInotifyEvent
}
