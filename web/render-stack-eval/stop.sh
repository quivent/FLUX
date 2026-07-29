#!/usr/bin/env bash
cd "$(dirname "$0")"

stopped=0
for f in .run/go.pid .run/rust.pid .run/jinja.pid; do
  if [ -f "$f" ]; then
    pid="$(cat "$f")"
    if kill "$pid" 2>/dev/null; then
      stopped=$((stopped + 1))
    fi
    rm -f "$f"
  fi
done

echo "Stopped ${stopped} process(es)."
