from __future__ import annotations

import socket
import sys
import time


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: probe.py HOST PORT", file=sys.stderr)
        return 2
    host, port_raw = sys.argv[1], sys.argv[2]
    port = int(port_raw)
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=2.0):
            reachable = True
            error = ""
    except Exception as exc:
        reachable = False
        error = f"{type(exc).__name__}: {exc}"
    elapsed_ms = (time.perf_counter() - started) * 1000
    print(f"reachable={str(reachable).lower()} elapsed_ms={elapsed_ms:.3f} error={error}")
    return 0 if reachable else 1


if __name__ == "__main__":
    raise SystemExit(main())
