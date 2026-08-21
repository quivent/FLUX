// Package arcane holds the Arcane pipeline's model registry: the compiled-in
// hardware roster, and a parser for the subset of TOML that
// jury_continuum.toml actually uses.
//
// There is deliberately no TOML dependency in go.mod. The continuum file is
// hand-written configuration with a small, stable shape — tables, dotted
// sub-tables, inline tables, arrays, scalars, comments — and pulling a general
// TOML library into a CLI that otherwise depends on nothing but a SQLite
// driver is not a trade worth making. What this parser does not understand it
// reports as an error, and the registry falls back to the compiled roster
// rather than starting up with a half-read config.
package arcane

import (
	"errors"
	"fmt"
	"strconv"
	"strings"
)

// errIncomplete means the value ran off the end of the text it was given: an
// array or inline table that continues on the next line. The line loop appends
// the next line and retries rather than trying to pre-scan for balance, which
// would need its own string-aware scanner anyway.
var errIncomplete = errors.New("incomplete value")

// maxContinuation caps how many lines a single value may span. Without it, one
// unterminated quote swallows the rest of the file before failing.
const maxContinuation = 400

// ParseTOML reads the TOML subset used by jury_continuum.toml into a nested
// map. Tables are map[string]any, arrays are []any, integers are int64, floats
// are float64, booleans are bool, and everything else is a string.
func ParseTOML(src string) (map[string]any, error) {
	root := map[string]any{}
	current := root

	lines := strings.Split(strings.ReplaceAll(src, "\r\n", "\n"), "\n")
	for i := 0; i < len(lines); i++ {
		line := strings.TrimSpace(lines[i])
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}

		if strings.HasPrefix(line, "[") {
			table, err := tableHeader(root, line)
			if err != nil {
				return nil, fmt.Errorf("line %d: %w", i+1, err)
			}
			current = table
			continue
		}

		eq := indexOutsideQuotes(line, '=')
		if eq < 0 {
			return nil, fmt.Errorf("line %d: expected `key = value`, got %q", i+1, truncate(line, 48))
		}
		path, err := splitDottedKey(strings.TrimSpace(line[:eq]))
		if err != nil {
			return nil, fmt.Errorf("line %d: %w", i+1, err)
		}

		raw := line[eq+1:]
		value, err := parseValue(raw)
		for start := i; errors.Is(err, errIncomplete) && i+1 < len(lines) && i-start < maxContinuation; {
			i++
			raw += "\n" + lines[i]
			value, err = parseValue(raw)
		}
		if err != nil {
			return nil, fmt.Errorf("line %d: key %q: %w", i+1, strings.Join(path, "."), err)
		}

		leaf := descend(current, path[:len(path)-1])
		leaf[path[len(path)-1]] = value
	}
	return root, nil
}

// tableHeader resolves a `[a.b.c]` or `[[a.b]]` line to the table that
// subsequent keys belong to, creating intermediate tables as it goes.
func tableHeader(root map[string]any, line string) (map[string]any, error) {
	arrayOfTables := strings.HasPrefix(line, "[[")
	open := 1
	if arrayOfTables {
		open = 2
	}
	end := indexOutsideQuotes(line[open:], ']')
	if end < 0 {
		return nil, fmt.Errorf("unterminated table header %q", truncate(line, 48))
	}
	end += open

	path, err := splitDottedKey(strings.TrimSpace(line[open:end]))
	if err != nil {
		return nil, err
	}
	if len(path) == 0 {
		return nil, errors.New("empty table header")
	}
	if !arrayOfTables {
		return descend(root, path), nil
	}

	parent := descend(root, path[:len(path)-1])
	name := path[len(path)-1]
	entry := map[string]any{}
	existing, _ := parent[name].([]any)
	parent[name] = append(existing, entry)
	return entry, nil
}

// descend walks (creating as needed) a dotted table path. A path segment that
// currently holds an array of tables resolves to that array's last element, so
// `[[a]]` followed by `[a.b]` lands in the right place.
func descend(table map[string]any, path []string) map[string]any {
	for _, segment := range path {
		switch existing := table[segment].(type) {
		case map[string]any:
			table = existing
		case []any:
			if len(existing) > 0 {
				if last, ok := existing[len(existing)-1].(map[string]any); ok {
					table = last
					continue
				}
			}
			next := map[string]any{}
			table[segment] = next
			table = next
		default:
			next := map[string]any{}
			table[segment] = next
			table = next
		}
	}
	return table
}

// splitDottedKey splits `variants.bf16` or `"quoted.key".sub` on the dots that
// are not inside quotes, and unquotes each segment.
func splitDottedKey(key string) ([]string, error) {
	if key == "" {
		return nil, errors.New("empty key")
	}
	var (
		segments []string
		current  strings.Builder
		quote    byte
	)
	for i := 0; i < len(key); i++ {
		c := key[i]
		switch {
		case quote != 0:
			if c == quote {
				quote = 0
				continue
			}
			current.WriteByte(c)
		case c == '"' || c == '\'':
			quote = c
		case c == '.':
			segments = append(segments, strings.TrimSpace(current.String()))
			current.Reset()
		default:
			current.WriteByte(c)
		}
	}
	if quote != 0 {
		return nil, fmt.Errorf("unterminated quote in key %q", truncate(key, 48))
	}
	segments = append(segments, strings.TrimSpace(current.String()))
	for _, segment := range segments {
		if segment == "" {
			return nil, fmt.Errorf("empty segment in key %q", truncate(key, 48))
		}
	}
	return segments, nil
}

// indexOutsideQuotes finds the first target byte that is not inside a string.
func indexOutsideQuotes(s string, target byte) int {
	var quote byte
	for i := 0; i < len(s); i++ {
		c := s[i]
		switch {
		case quote != 0:
			if c == '\\' && quote == '"' {
				i++
				continue
			}
			if c == quote {
				quote = 0
			}
		case c == '"' || c == '\'':
			quote = c
		case c == target:
			return i
		case c == '#':
			return -1
		}
	}
	return -1
}

// parseValue reads exactly one value and asserts that only whitespace and an
// optional comment follow it.
func parseValue(raw string) (any, error) {
	p := &tomlScanner{src: raw}
	value, err := p.value()
	if err != nil {
		return nil, err
	}
	p.skip()
	if !p.eof() {
		return nil, fmt.Errorf("trailing text %q after value", truncate(p.src[p.pos:], 32))
	}
	return value, nil
}

type tomlScanner struct {
	src string
	pos int
}

func (p *tomlScanner) eof() bool { return p.pos >= len(p.src) }

// skip advances past whitespace, newlines, and comments.
func (p *tomlScanner) skip() {
	for !p.eof() {
		switch p.src[p.pos] {
		case ' ', '\t', '\n', '\r':
			p.pos++
		case '#':
			for !p.eof() && p.src[p.pos] != '\n' {
				p.pos++
			}
		default:
			return
		}
	}
}

func (p *tomlScanner) value() (any, error) {
	p.skip()
	if p.eof() {
		return nil, errIncomplete
	}
	switch p.src[p.pos] {
	case '"', '\'':
		return p.stringValue()
	case '[':
		return p.arrayValue()
	case '{':
		return p.inlineTable()
	default:
		return p.scalarValue()
	}
}

func (p *tomlScanner) stringValue() (any, error) {
	quote := p.src[p.pos]
	triple := strings.Repeat(string(quote), 3)
	if strings.HasPrefix(p.src[p.pos:], triple) {
		p.pos += 3
		end := strings.Index(p.src[p.pos:], triple)
		if end < 0 {
			return nil, errIncomplete
		}
		body := strings.TrimPrefix(p.src[p.pos:p.pos+end], "\n")
		p.pos += end + 3
		if quote == '\'' {
			return body, nil
		}
		return unescape(body)
	}

	p.pos++
	var out strings.Builder
	for {
		if p.eof() {
			return nil, errIncomplete
		}
		c := p.src[p.pos]
		switch {
		case c == '\n':
			return nil, errors.New("unterminated string")
		case c == quote:
			p.pos++
			if quote == '\'' {
				return out.String(), nil
			}
			return unescape(out.String())
		case c == '\\' && quote == '"':
			out.WriteByte(c)
			p.pos++
			if p.eof() {
				return nil, errIncomplete
			}
			out.WriteByte(p.src[p.pos])
			p.pos++
		default:
			out.WriteByte(c)
			p.pos++
		}
	}
}

func (p *tomlScanner) arrayValue() (any, error) {
	p.pos++ // consume [
	out := []any{}
	for {
		p.skip()
		if p.eof() {
			return nil, errIncomplete
		}
		if p.src[p.pos] == ']' {
			p.pos++
			return out, nil
		}
		item, err := p.value()
		if err != nil {
			return nil, err
		}
		out = append(out, item)
		p.skip()
		if p.eof() {
			return nil, errIncomplete
		}
		switch p.src[p.pos] {
		case ',':
			p.pos++
		case ']':
			p.pos++
			return out, nil
		default:
			return nil, fmt.Errorf("expected `,` or `]` in array, got %q", string(p.src[p.pos]))
		}
	}
}

func (p *tomlScanner) inlineTable() (any, error) {
	p.pos++ // consume {
	out := map[string]any{}
	for {
		p.skip()
		if p.eof() {
			return nil, errIncomplete
		}
		if p.src[p.pos] == '}' {
			p.pos++
			return out, nil
		}

		eq := indexOutsideQuotes(p.src[p.pos:], '=')
		if eq < 0 {
			return nil, errIncomplete
		}
		path, err := splitDottedKey(strings.TrimSpace(p.src[p.pos : p.pos+eq]))
		if err != nil {
			return nil, err
		}
		p.pos += eq + 1

		item, err := p.value()
		if err != nil {
			return nil, err
		}
		leaf := descend(out, path[:len(path)-1])
		leaf[path[len(path)-1]] = item

		p.skip()
		if p.eof() {
			return nil, errIncomplete
		}
		switch p.src[p.pos] {
		case ',':
			p.pos++
		case '}':
			p.pos++
			return out, nil
		default:
			return nil, fmt.Errorf("expected `,` or `}` in inline table, got %q", string(p.src[p.pos]))
		}
	}
}

// scalarValue reads a bare token and types it. Anything that is not a
// recognisable bool, integer, or float stays a string — dates and the odd
// unquoted token survive instead of failing the whole file.
func (p *tomlScanner) scalarValue() (any, error) {
	start := p.pos
	for !p.eof() {
		c := p.src[p.pos]
		if c == ',' || c == ']' || c == '}' || c == '\n' || c == '#' {
			break
		}
		p.pos++
	}
	token := strings.TrimSpace(p.src[start:p.pos])
	if token == "" {
		return nil, errIncomplete
	}
	switch token {
	case "true":
		return true, nil
	case "false":
		return false, nil
	}
	clean := strings.ReplaceAll(token, "_", "")
	if i, err := strconv.ParseInt(clean, 0, 64); err == nil {
		return i, nil
	}
	if f, err := strconv.ParseFloat(clean, 64); err == nil {
		return f, nil
	}
	return token, nil
}

func unescape(s string) (string, error) {
	if !strings.Contains(s, "\\") {
		return s, nil
	}
	var out strings.Builder
	for i := 0; i < len(s); i++ {
		if s[i] != '\\' {
			out.WriteByte(s[i])
			continue
		}
		i++
		if i >= len(s) {
			return "", errors.New("dangling escape in string")
		}
		switch s[i] {
		case 'n':
			out.WriteByte('\n')
		case 't':
			out.WriteByte('\t')
		case 'r':
			out.WriteByte('\r')
		case '"':
			out.WriteByte('"')
		case '\\':
			out.WriteByte('\\')
		case 'b':
			out.WriteByte('\b')
		case 'f':
			out.WriteByte('\f')
		case 'u', 'U':
			width := 4
			if s[i] == 'U' {
				width = 8
			}
			if i+width >= len(s) {
				return "", errors.New("truncated unicode escape")
			}
			code, err := strconv.ParseUint(s[i+1:i+1+width], 16, 32)
			if err != nil {
				return "", fmt.Errorf("bad unicode escape: %w", err)
			}
			out.WriteRune(rune(code))
			i += width
		default:
			return "", fmt.Errorf("unknown escape \\%s", string(s[i]))
		}
	}
	return out.String(), nil
}

func truncate(s string, n int) string {
	s = strings.TrimSpace(s)
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}

// ---------------------------------------------------------------------------
// Typed accessors. Every one of these tolerates a missing or wrongly-typed key
// so a half-written config degrades field by field instead of all at once.
// ---------------------------------------------------------------------------

// TableAt resolves a dotted path to a nested table.
func TableAt(root map[string]any, path ...string) (map[string]any, bool) {
	current := root
	for _, segment := range path {
		next, ok := current[segment].(map[string]any)
		if !ok {
			return nil, false
		}
		current = next
	}
	return current, true
}

// Str reads a string key, returning fallback when absent or empty.
func Str(table map[string]any, key, fallback string) string {
	if v, ok := table[key].(string); ok && strings.TrimSpace(v) != "" {
		return v
	}
	return fallback
}

// Num reads any numeric key as a float, returning fallback when absent.
func Num(table map[string]any, key string, fallback float64) float64 {
	switch v := table[key].(type) {
	case float64:
		return v
	case int64:
		return float64(v)
	case int:
		return float64(v)
	}
	return fallback
}

// Whole reads a numeric key as an int, returning fallback when absent.
func Whole(table map[string]any, key string, fallback int) int {
	switch v := table[key].(type) {
	case int64:
		return int(v)
	case int:
		return v
	case float64:
		return int(v)
	}
	return fallback
}

// Flag reads a boolean key, returning fallback when absent.
func Flag(table map[string]any, key string, fallback bool) bool {
	if v, ok := table[key].(bool); ok {
		return v
	}
	return fallback
}

// Strings reads an array key as a string slice, skipping non-string members.
func Strings(table map[string]any, key string) []string {
	items, ok := table[key].([]any)
	if !ok {
		return nil
	}
	out := make([]string, 0, len(items))
	for _, item := range items {
		if s, ok := item.(string); ok {
			out = append(out, s)
		}
	}
	return out
}
