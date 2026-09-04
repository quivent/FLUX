package server

import "net/http"

// teaGovernorLaw is house law for GPU 1. The Governor trains himself.
// Sounding boards and Qwen (reason) are not teachers. He is protected,
// must not break, keeps his tools, and keeps autonomy.
func teaGovernorLaw() map[string]any {
	return map[string]any{
		"schema":           "tea.governor-law.v1",
		"gpu":              1,
		"trains_himself":   true,
		"sounding_boards":  true,
		"qwen":             "reason",
		"qwen_gpu":         2,
		"protected":        true,
		"do_not_break":     true,
		"tools":            true,
		"autonomy":         true,
		"reliability":      "100%",
		"memory_expansion": true,
		"reasoning":        "qwen",
		"training_focus":   []string{"reliability", "memory_expansion", "reasoning_performance"},
		"never_kill":       []string{"governor-engine", "governor-gateway", "hive-research"},
		"line":             "Train for 100% reliability, expansion of memory, and performance of reasoning. Protected. Do not break. Keep his tools. Leave him autonomy. He trains himself. Qwen is reason.",
	}
}

func (s Server) teaLawAPI(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	writeJSON(w, http.StatusOK, teaGovernorLaw())
}
