from __future__ import annotations

import subprocess
import sys

from common import MODES, ROOT

# 4개 모드를 순회하며 구조적 보안효과(4.1/4.2) 실험 전체를 자동 수행하는
# 오케스트레이터. 이 저장소가 Windows/PowerShell 환경에서 주로 쓰이므로 bash
# 스크립트(run_all.sh) 대신 순수 Python으로 작성한다.
#
# 각 모드: compose up --wait -> 사전검증(정책/CDS 시나리오, --fail-on-mismatch로
# 실패 시 즉시 중단) -> 90쌍 도달성 전수검사 -> 80조합 정책공간 탐색 ->
# compose down -v. 전 모드 완료 후 Network/Policy/Effective 그래프 구성 및
# AOD/MPL/TINR 분석을 자동 호출한다.
#
# 환경 메타데이터(collect_env.py)는 루프 시작 전 딱 한 번만 수집한다 — 루프
# 안에서 매 모드마다 수집하면 이미 preflight가 써 놓은 raw CSV/감사로그
# 때문에 git status가 항상 dirty로 잡혀서(소스 코드는 그대로인데도) git_dirty
# 필드가 재현성 메타데이터로서 의미가 없어진다.

PYTHON = sys.executable


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=ROOT)
    if check and proc.returncode != 0:
        raise SystemExit(f"command failed (exit {proc.returncode}): {' '.join(cmd)}")
    return proc


def compose_up(mode: str) -> None:
    run(["docker", "compose", "-f", f"compose.{mode}.yml", "up", "-d", "--build", "--wait"])


def compose_down(mode: str) -> None:
    run(["docker", "compose", "-f", f"compose.{mode}.yml", "down", "-v"], check=False)


def main() -> int:
    print("[env] collecting metadata (once, before any experiment output is written)")
    run([PYTHON, "scripts/collect_env.py", "--results-dir", "results"])

    for mode in MODES:
        print(f"\n===== mode={mode} =====")
        compose_up(mode)

        # 사전검증/테스트 중 하나라도 실패하면 스택을 내리지 않고 즉시 중단한다 —
        # 원인 조사를 위해 실패 상태를 그대로 남겨둔다(down -v로 조용히 지우지 않음).
        print(f"[preflight] policy scenarios ({mode})")
        preflight_policy = run([PYTHON, "scripts/run_policy_tests.py", "--mode", mode, "--repeat", "1", "--fail-on-mismatch"], check=False)
        if preflight_policy.returncode != 0:
            raise SystemExit(f"preflight policy scenarios failed for mode={mode}; stack left running for inspection (compose.{mode}.yml)")

        print(f"[preflight] CDS scenarios ({mode})")
        preflight_cds = run([PYTHON, "scripts/run_cds_tests.py", "--mode", mode, "--repeat", "1", "--fail-on-mismatch"], check=False)
        if preflight_cds.returncode != 0:
            raise SystemExit(f"preflight CDS scenarios failed for mode={mode}; stack left running for inspection (compose.{mode}.yml)")

        print(f"[network] exhaustive 90-pair reachability ({mode})")
        reach = run([PYTHON, "scripts/run_reachability_tests.py", "--mode", mode], check=False)
        if reach.returncode != 0:
            raise SystemExit(f"reachability test found unstable pair(s) for mode={mode}; stack left running for inspection")

        print(f"[policy] 80-combination policy space ({mode})")
        pspace = run([PYTHON, "scripts/run_policy_space.py", "--mode", mode], check=False)
        if pspace.returncode != 0:
            raise SystemExit(f"policy space test hit execution error(s) for mode={mode}; stack left running for inspection")

        compose_down(mode)

    print("\n===== building network/policy/effective graphs =====")
    run([PYTHON, "scripts/build_effective_graph.py"])
    print("\n===== analyzing AOD/MPL/TINR =====")
    run([PYTHON, "scripts/analyze_graph_metrics.py"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
