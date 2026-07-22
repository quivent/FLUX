package history

import (
	"bufio"
	"encoding/json"
	"os"
	"time"
)

type Entry struct {
	Time     time.Time `json:"time"`
	Prompt   string    `json:"prompt"`
	Style    string    `json:"style,omitempty"`
	Mood     string    `json:"mood,omitempty"`
	Width    int       `json:"width"`
	Height   int       `json:"height"`
	Steps    int       `json:"steps"`
	Guidance float64   `json:"guidance"`
	Seed     string    `json:"seed,omitempty"`
	Output   string    `json:"output,omitempty"`
	Seconds  string    `json:"seconds,omitempty"`
}

func Append(path string, e Entry) error {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	defer f.Close()
	return json.NewEncoder(f).Encode(e)
}

func Last(path string, n int) ([]Entry, error) {
	f, err := os.Open(path)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var out []Entry
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		var e Entry
		if json.Unmarshal(sc.Bytes(), &e) == nil {
			out = append(out, e)
		}
	}
	if err := sc.Err(); err != nil {
		return nil, err
	}
	if n > 0 && len(out) > n {
		out = out[len(out)-n:]
	}
	return out, nil
}
