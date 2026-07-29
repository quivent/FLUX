#!/usr/bin/env python3
"""Concurrent SSE latency probe: opens N connections to /events on each
server, measures server-timestamp -> client-receipt latency for the first
event each connection receives, reports p50/p95/p99 and failure count.
Mirrors the methodology in ~/Oscillihue/docs/RENDER_STACK_PERFORMANCE_EVALUATION_2026-07-29.md.

Handles chunked transfer-encoding (all three servers use it for SSE), since
a naive readline() right after headers would read a chunk-size line
("1a\\r\\n") instead of the actual "data: ..." payload.
"""
import asyncio
import json
import sys
import time

TARGETS = {"jinja": 9201, "go": 9202, "rust": 9203}


async def read_headers(reader):
    headers = []
    while True:
        line = await asyncio.wait_for(reader.readline(), timeout=5)
        if line in (b"\r\n", b""):
            break
        headers.append(line.decode(errors="ignore").strip())
    return headers


async def read_sse_data_line(reader, chunked):
    """Returns the first non-empty 'data: ...' line, whether or not the
    transport is chunked."""
    while True:
        if chunked:
            size_line = await asyncio.wait_for(reader.readline(), timeout=5)
            size_line = size_line.strip()
            if not size_line:
                continue
            try:
                size = int(size_line, 16)
            except ValueError:
                continue
            if size == 0:
                raise EOFError("chunked stream ended")
            chunk = await asyncio.wait_for(reader.readexactly(size), timeout=5)
            await asyncio.wait_for(reader.readline(), timeout=5)  # trailing \r\n
            text = chunk.decode(errors="ignore").strip()
        else:
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            text = line.decode(errors="ignore").strip()
        if text.startswith("data:"):
            return text
        if not text:
            continue


async def one_conn(host, port, results, failures):
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=5
        )
        req = f"GET /events HTTP/1.1\r\nHost: {host}\r\nConnection: keep-alive\r\n\r\n"
        writer.write(req.encode())
        await writer.drain()

        headers = await read_headers(reader)
        chunked = any("transfer-encoding" in h.lower() and "chunked" in h.lower() for h in headers)

        text = await read_sse_data_line(reader, chunked)
        recv_ns = time.time_ns()
        payload = json.loads(text[5:].strip())
        sent_ns = int(payload["ts"])
        latency_ms = (recv_ns - sent_ns) / 1_000_000
        results.append(abs(latency_ms))
        writer.close()
    except Exception as e:
        failures[0] += 1


async def run_level(port, n):
    results = []
    failures = [0]
    tasks = [one_conn("127.0.0.1", port, results, failures) for _ in range(n)]
    await asyncio.gather(*tasks)
    return results, failures[0]


async def main():
    levels = [1, 50, 500, 2000, 5000]
    print(f"{'stack':<8}{'n':>6}{'p50(ms)':>10}{'p95(ms)':>10}{'p99(ms)':>10}{'fail':>7}")
    for name, port in TARGETS.items():
        for n in levels:
            results, failures = await run_level(port, n)
            if not results:
                print(f"{name:<8}{n:>6}{'--':>10}{'--':>10}{'--':>10}{failures:>7}")
                continue
            results.sort()
            p50 = results[max(int(len(results) * 0.50) - 1, 0)]
            p95 = results[max(int(len(results) * 0.95) - 1, 0)]
            p99 = results[max(int(len(results) * 0.99) - 1, 0)]
            print(f"{name:<8}{n:>6}{p50:>10.1f}{p95:>10.1f}{p99:>10.1f}{failures:>7}")


if __name__ == "__main__":
    asyncio.run(main())
