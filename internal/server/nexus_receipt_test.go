package server

import "testing"

func TestNexusReceiptVerified(t *testing.T) {
	valid := map[string]any{
		"ok": true, "accepted": true, "verified": true,
		"status": "accepted", "job_id": "atlas-1", "receipt_id": "nexus-atlas-1-proof",
	}
	if !nexusReceiptVerified("atlas-1", valid) {
		t.Fatal("expected a matching durable receipt to verify")
	}
	for name, mutate := range map[string]func(map[string]any){
		"wrong job":       func(row map[string]any) { row["job_id"] = "atlas-2" },
		"missing receipt": func(row map[string]any) { row["receipt_id"] = "" },
		"not verified":    func(row map[string]any) { row["verified"] = false },
		"not accepted":    func(row map[string]any) { row["accepted"] = false },
		"wrong status":    func(row map[string]any) { row["status"] = "queued" },
	} {
		t.Run(name, func(t *testing.T) {
			row := make(map[string]any, len(valid))
			for key, value := range valid {
				row[key] = value
			}
			mutate(row)
			if nexusReceiptVerified("atlas-1", row) {
				t.Fatal("invalid receipt verified")
			}
		})
	}
}
