package cdn

import (
	"net/url"
	"os"
	"path/filepath"
	"strings"
)

const DefaultPublicBase = "https://pub-197bed319eda457da858ab89c061ed38.r2.dev/site"

func PublicBase() string {
	v := strings.TrimSpace(os.Getenv("TEA_R2_PUBLIC"))
	if v == "" {
		v = DefaultPublicBase
	}
	return strings.TrimRight(v, "/")
}

func AssetURL(rel string) string {
	rel = strings.Trim(filepath.ToSlash(rel), "/")
	if rel == "" || rel == "." {
		return PublicBase() + "/"
	}
	parts := strings.Split(rel, "/")
	for i, part := range parts {
		parts[i] = url.PathEscape(part)
	}
	return PublicBase() + "/" + strings.Join(parts, "/")
}

func ShippedRel(rel string) bool {
	rel = strings.Trim(filepath.ToSlash(rel), "/")
	return strings.HasPrefix(rel, "collections/")
}
