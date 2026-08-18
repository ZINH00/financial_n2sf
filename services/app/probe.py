from __future__ import annotations

import json
import socket
import sys
import time


def check_one(host: str, port: int) -> tuple[bool, float, str]:
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=2.0):
            reachable = True
            error = ""
    except Exception as exc:
        reachable = False
        error = f"{type(exc).__name__}: {exc}"
    elapsed_ms = (time.perf_counter() - started) * 1000
    return reachable, elapsed_ms, error


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: probe.py HOST PORT | probe.py HOST:PORT [HOST:PORT ...]", file=sys.stderr)
        return 2

    # 인자가 정확히 2개고 두 번째가 숫자면 기존 단일 HOST PORT 호출과 하위호환을
    # 유지한다. 그 외에는 HOST:PORT 다중 타겟으로 해석해 컨테이너 하나를 여러 번
    # 띄우지 않고 한 번에 여러 목적지를 검사한다(자산 10개당 1회만 컨테이너를
    # 기동하기 위함). 결과는 JSON Lines로 출력한다 — 에러 메시지에 공백/콜론이
    # 섞여도 호출측 파싱이 안전하도록 하기 위함이다.
    if len(sys.argv) == 3 and sys.argv[2].isdigit():
        targets = [(sys.argv[1], int(sys.argv[2]))]
        labels = [f"{sys.argv[1]}:{sys.argv[2]}"]
    else:
        targets = []
        labels = []
        for arg in sys.argv[1:]:
            host, _, port_raw = arg.rpartition(":")
            if not host or not port_raw.isdigit():
                print(json.dumps({"target": arg, "reachable": False, "elapsed_ms": 0.0, "error": "invalid target (expected HOST:PORT)"}))
                return 2
            targets.append((host, int(port_raw)))
            labels.append(arg)

    any_unreachable = False
    for label, (host, port) in zip(labels, targets):
        reachable, elapsed_ms, error = check_one(host, port)
        if not reachable:
            any_unreachable = True
        print(json.dumps({"target": label, "reachable": reachable, "elapsed_ms": round(elapsed_ms, 3), "error": error}))
    return 1 if any_unreachable else 0


if __name__ == "__main__":
    raise SystemExit(main())
