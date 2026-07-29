# Run the three motion-atlas render-stack comparison implementations.
# Mirrors ~/Oscillihue/studio/. None of this is wired into FLUX's production
# server (internal/server/server.go).

JINJA_PORT ?= 9201
GO_PORT ?= 9202
RUST_PORT ?= 9203

.PHONY: install build-go build-rust run-jinja run-go run-rust dev stop clean

install:
	python3 -m venv jinja_fastapi/.venv
	jinja_fastapi/.venv/bin/pip install -q --upgrade pip
	jinja_fastapi/.venv/bin/pip install -q fastapi uvicorn

build-go:
	cd go_html_template && go build -o server .

build-rust:
	cd rust_axum && cargo build --release

run-jinja: install
	cd jinja_fastapi && ../jinja_fastapi/.venv/bin/uvicorn main:app --host 127.0.0.1 --port $(JINJA_PORT)

run-go: build-go
	cd go_html_template && ./server

run-rust: build-rust
	cd rust_axum && ./target/release/rust_axum

dev: build-rust build-go install
	@JINJA_PORT=$(JINJA_PORT) GO_PORT=$(GO_PORT) RUST_PORT=$(RUST_PORT) ./dev.sh

stop:
	@./stop.sh

clean:
	rm -rf go_html_template/server rust_axum/target jinja_fastapi/.venv .run
