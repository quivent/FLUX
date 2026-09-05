package cdn

import "testing"

func TestShippedRel(t *testing.T) {
	if !ShippedRel("collections/silken-horses/a.png") {
		t.Fatal("collections rel must ship")
	}
	if ShippedRel("protocol-fashion-001.png") {
		t.Fatal("root fashion is not the collection pack")
	}
	if ShippedRel("arcane/x.png") {
		t.Fatal("arcane is not the collection pack")
	}
}

func TestAssetURL(t *testing.T) {
	got := AssetURL("collections/silken-horses/protocol-silken-horses-stream-20260904-234316-265.png")
	want := DefaultPublicBase + "/collections/silken-horses/protocol-silken-horses-stream-20260904-234316-265.png"
	if got != want {
		t.Fatalf("got %s want %s", got, want)
	}
}
