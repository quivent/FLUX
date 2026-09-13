package main

import "testing"

func TestBeautyArchitectureProfiles(t *testing.T) {
	want := map[string]string{
		"compact":     "h100",
		"remote-qwen": "h100-remote-witness",
		"distributed": "h100-distributed-atelier",
	}
	for architecture, profile := range want {
		got, err := beautyProfile(architecture)
		if err != nil || got != profile {
			t.Fatalf("beautyProfile(%q) = %q, %v; want %q", architecture, got, err, profile)
		}
	}
}

func TestBeautyArchitectureRejectsUnknown(t *testing.T) {
	if _, err := beautyProfile("pixtral-remote"); err == nil {
		t.Fatal("expected pixtral-remote to be rejected")
	}
}
