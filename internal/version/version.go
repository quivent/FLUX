package version

import (
	"fmt"
	"runtime/debug"
	"strings"
	"time"
)

var (
	// Version is injected via ldflags during build, or defaults to the base release version.
	Version = "2026.8.19"
	// BuildTime is injected via ldflags or auto-detected from VCS build info / runtime.
	BuildTime = ""
	// GitCommit is injected via ldflags or auto-detected from VCS info.
	GitCommit = ""
	// BuildNum tracks the sequential build generation number.
	BuildNum = ""
)

func init() {
	if info, ok := debug.ReadBuildInfo(); ok {
		for _, setting := range info.Settings {
			switch setting.Key {
			case "vcs.revision":
				if GitCommit == "" {
					GitCommit = setting.Value
				}
			case "vcs.time":
				if BuildTime == "" {
					BuildTime = setting.Value
				}
			case "vcs.modified":
				if setting.Value == "true" && GitCommit != "" && !strings.HasSuffix(GitCommit, "-dirty") {
					GitCommit += "-dirty"
				}
			}
		}
	}
	if BuildTime == "" {
		BuildTime = time.Now().UTC().Format("2006-01-02T15:04:05Z")
	}
	if GitCommit == "" {
		GitCommit = "dev"
	}
}

// Full returns the human-readable reversion string including version, build number, commit hash, and build timestamp.
func Full() string {
	commit := GitCommit
	dirty := ""
	if strings.HasSuffix(commit, "-dirty") {
		dirty = "-dirty"
		commit = strings.TrimSuffix(commit, "-dirty")
	}
	if len(commit) > 7 {
		commit = commit[:7]
	}
	shortCommit := commit + dirty
	buildPart := ""
	if BuildNum != "" {
		buildPart = fmt.Sprintf("b%s · ", BuildNum)
	}
	return fmt.Sprintf("v%s (%s%s · %s)", Version, buildPart, shortCommit, BuildTime)
}

// String returns the bare version string.
func String() string {
	return Version
}
