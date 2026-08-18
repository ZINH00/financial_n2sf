from __future__ import annotations

import argparse
import subprocess
import sys

from common import MODES, ROOT

# 논문 3.4.2 성능 평가 오케스트레이터: 네 실험조건을 독립적으로 재기동하는
# N개(기본 12) 반복 라운드를 수행하고, 각 조건이 실행순서의 동일 위치에
# 배치되도록 순서를 균형화한다(cyclic Latin square — 라운드마다 시작 조건을
# 한 칸씩 돌리는 결정론적·재현 가능한 균형화 기법). 12라운드는 4조건 순환을
# 3바퀴 도는 것과 같아, 각 조건이 4개 실행위치(1~4번째)에 정확히 3회씩 배치된다.

PYTHON = sys.executable


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=ROOT)
    if check and proc.returncode != 0:
        raise SystemExit(f"command failed (exit {proc.returncode}): {' '.join(cmd)}")
    return proc


def cyclic_order(round_id: int) -> list[str]:
    modes = list(MODES)
    shift = (round_id - 1) % len(modes)
    return modes[shift:] + modes[:shift]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the balanced-order 12-round independent performance experiment across all 4 ablation modes.")
    parser.add_argument("--rounds", type=int, default=12)
    parser.add_argument("--batches", type=int, default=3)
    parser.add_argument("--per-batch", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=20)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("--rounds must be >= 1")

    print("===== building images once before the round sweep =====")
    for mode in MODES:
        run(["docker", "compose", "-f", f"compose.{mode}.yml", "build"])

    for round_id in range(1, args.rounds + 1):
        order = cyclic_order(round_id)
        print(f"\n===== round {round_id}/{args.rounds}: order={order} =====")
        for position, mode in enumerate(order, start=1):
            print(f"--- round={round_id} position={position} mode={mode} ---")
            run(["docker", "compose", "-f", f"compose.{mode}.yml", "up", "-d", "--wait"])
            try:
                run([
                    PYTHON, "scripts/run_performance_tests.py",
                    "--mode", mode,
                    "--round-id", str(round_id),
                    "--order-position", str(position),
                    "--batches", str(args.batches),
                    "--per-batch", str(args.per_batch),
                    "--warmup", str(args.warmup),
                ])
            finally:
                run(["docker", "compose", "-f", f"compose.{mode}.yml", "down", "-v"], check=False)

    print(f"\nDone. {args.rounds} rounds x {len(MODES)} conditions completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
