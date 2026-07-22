//go:build darwin

package server

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"syscall"
)

func waitForPathChange(ctx context.Context, target string) bool {
	dir := filepath.Dir(target)
	fd, err := syscall.Open(dir, syscall.O_RDONLY, 0)
	if err != nil {
		return false
	}
	defer syscall.Close(fd)

	kq, err := syscall.Kqueue()
	if err != nil {
		return false
	}
	defer syscall.Close(kq)

	watch := syscall.Kevent_t{
		Ident:  uint64(fd),
		Filter: syscall.EVFILT_VNODE,
		Flags:  syscall.EV_ADD | syscall.EV_CLEAR,
		Fflags: syscall.NOTE_WRITE | syscall.NOTE_RENAME | syscall.NOTE_DELETE | syscall.NOTE_EXTEND | syscall.NOTE_ATTRIB,
	}
	if _, err := syscall.Kevent(kq, []syscall.Kevent_t{watch}, nil, nil); err != nil {
		return false
	}

	done := make(chan struct{})
	go func() {
		select {
		case <-ctx.Done():
			_ = syscall.Close(kq)
		case <-done:
		}
	}()
	events := make([]syscall.Kevent_t, 1)
	n, err := syscall.Kevent(kq, nil, events, nil)
	close(done)
	return err == nil && n > 0
}

func waitForTreeChange(ctx context.Context, root string) bool {
	rootAbs, err := filepath.Abs(root)
	if err != nil {
		return false
	}
	kq, err := syscall.Kqueue()
	if err != nil {
		return false
	}
	defer syscall.Close(kq)

	fds := make([]int, 0)
	defer func() {
		for _, fd := range fds {
			_ = syscall.Close(fd)
		}
	}()
	watches := make([]syscall.Kevent_t, 0)
	err = filepath.WalkDir(rootAbs, func(file string, entry os.DirEntry, err error) error {
		if err != nil || !entry.IsDir() {
			return nil
		}
		if file != rootAbs && strings.HasPrefix(entry.Name(), ".") {
			return filepath.SkipDir
		}
		fd, err := syscall.Open(file, syscall.O_RDONLY, 0)
		if err != nil {
			return nil
		}
		fds = append(fds, fd)
		watches = append(watches, syscall.Kevent_t{
			Ident:  uint64(fd),
			Filter: syscall.EVFILT_VNODE,
			Flags:  syscall.EV_ADD | syscall.EV_CLEAR,
			Fflags: syscall.NOTE_WRITE | syscall.NOTE_RENAME | syscall.NOTE_DELETE | syscall.NOTE_EXTEND | syscall.NOTE_ATTRIB,
		})
		return nil
	})
	if err != nil || len(watches) == 0 {
		return false
	}
	if _, err := syscall.Kevent(kq, watches, nil, nil); err != nil {
		return false
	}

	done := make(chan struct{})
	go func() {
		select {
		case <-ctx.Done():
			_ = syscall.Close(kq)
		case <-done:
		}
	}()
	events := make([]syscall.Kevent_t, 8)
	n, err := syscall.Kevent(kq, nil, events, nil)
	close(done)
	return err == nil && n > 0
}
