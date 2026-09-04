package server

import (
	"os"
	"path/filepath"
	"testing"
)

func TestAttachBeautyDifferentialsUsesPreviousComposite(t *testing.T) {
	dir := t.TempDir()
	audit := filepath.Join(dir, "audit.jsonl")
	body := `{"image_path":"` + dir + `/a-001.png","composite":80.0,"unscored":false}
{"image_path":"` + dir + `/a-002.png","composite":80.3,"unscored":false}
{"image_path":"` + dir + `/a-003.png","composite":80.0,"unscored":true}
`
	if err := os.WriteFile(audit, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	items := []recentImage{
		{Name: "a-002.png", Path: "/outputs/a-002.png"},
		{Name: "a-001.png", Path: "/outputs/a-001.png"},
		{Name: "a-003.png", Path: "/outputs/a-003.png"},
	}
	attachBeautyDifferentials(dir, items)
	if items[1].Composite == nil || *items[1].Composite != 80 {
		t.Fatalf("first scored %+v", items[1].Composite)
	}
	if items[1].BeautyDeltaPct != nil {
		t.Fatalf("first frame should have no delta")
	}
	if items[0].BeautyDeltaPct == nil {
		t.Fatal("second frame missing delta")
	}
	if d := *items[0].BeautyDeltaPct; d < 0.29 || d > 0.31 {
		t.Fatalf("delta %v want +0.3", d)
	}
	if items[2].Composite != nil || items[2].BeautyDeltaPct != nil {
		t.Fatalf("unscored should stay empty %+v %+v", items[2].Composite, items[2].BeautyDeltaPct)
	}
}
