from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from collections import defaultdict

from common import MODES, RESULTS, ROOT, read_csv, timestamp, write_csv

# 논문 3.4.1은 10개 업무자산(5개 업무 x app/db) 사이의 90개 방향성 관계를 전수검사
# 하도록 요구한다. 자산별로 별도 프로브 컨테이너를 90번 띄우는 대신, 출발 자산당
# 1회만 컨테이너를 기동해 나머지 9개 목적지를 한 번에 검사한다(probe.py의 다중
# 타겟 지원). 프로브 컨테이너는 `docker run --network container:<source_id>`로
# 출발 자산의 네트워크 네임스페이스를 그대로 공유하므로, DB 이미지(postgres,
# 파이썬 없음)를 건드리지 않고도 DB 컨테이너를 "출발지"로 취급할 수 있다.
ATTEMPTS = 3


def run_cmd(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False, timeout=timeout)


def container_id(compose_file: str, service: str) -> str:
    proc = run_cmd(["docker", "compose", "-f", compose_file, "ps", "-q", service], timeout=15)
    cid = proc.stdout.strip()
    if not cid:
        raise RuntimeError(f"container not found for service={service} (stack not up? compose_file={compose_file}) stderr={proc.stderr}")
    return cid


def probe_image(compose_file: str) -> str:
    # customer_app 이미지를 재사용한다 — 5개 업무 App 이미지는 동일한 Dockerfile로
    # 빌드되어 모두 python+probe.py를 포함하므로 어느 것을 골라도 무방하다.
    cid = container_id(compose_file, "customer_app")
    proc = run_cmd(["docker", "inspect", "--format", "{{.Config.Image}}", cid], timeout=15)
    image = proc.stdout.strip()
    if not image:
        raise RuntimeError(f"could not resolve probe image from container {cid}: {proc.stderr}")
    return image


def probe_once(image: str, source_container_id: str, targets: list[tuple[str, str, int]]) -> dict[str, dict]:
    """단일 컨테이너 기동으로 여러 목적지를 한 번에 검사한다.
    targets: (asset_id, host, port) 목록. 반환: asset_id -> {"reachable":..,"elapsed_ms":..,"error":..}
    """
    args = [f"{host}:{port}" for _, host, port in targets]
    cmd = ["docker", "run", "--rm", "--network", f"container:{source_container_id}", image, "python", "probe.py", *args]
    proc = run_cmd(cmd, timeout=max(30.0, 5.0 * len(targets)))
    by_label: dict[str, dict] = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        by_label[rec["target"]] = rec
    results: dict[str, dict] = {}
    for asset_id, host, port in targets:
        label = f"{host}:{port}"
        rec = by_label.get(label)
        if rec is None:
            # probe.py가 이 타겟에 대한 JSON 결과 줄을 아예 내지 않은 경우다(예:
            # 컨테이너 자체가 뜨지 못함, 중간에 죽음, 예상 밖 stdout). 이걸
            # reachable=False로 채워버리면 "probe 실행 자체가 실패"와 "실제로
            # 네트워크가 막혀 있음"을 구분할 수 없게 되고, 3회 모두 같은 방식으로
            # 실패하면 stable=true로 통과해 버린다. 실행 오류는 네트워크 결과로
            # 둔갑시키지 않고 즉시 실패시킨다.
            raise RuntimeError(
                f"probe execution failed for target={label} (rc={proc.returncode}): "
                f"stdout={proc.stdout.strip()!r} stderr={proc.stderr.strip()!r}"
            )
        results[asset_id] = {"reachable": bool(rec["reachable"]), "elapsed_ms": float(rec["elapsed_ms"]), "error": rec.get("error", "")}
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Exhaustively test direct TCP reachability across all 90 directed business-asset pairs.")
    parser.add_argument("--mode", choices=list(MODES), required=True)
    parser.add_argument("--attempts", type=int, default=ATTEMPTS)
    args = parser.parse_args()
    if args.attempts < 1:
        parser.error("--attempts must be >= 1")

    compose_file = str(ROOT / f"compose.{args.mode}.yml")
    assets = read_csv("assets.csv")
    asset_by_id = {a["asset_id"]: a for a in assets}

    image = probe_image(compose_file)
    source_ids: dict[str, str] = {}
    for asset in assets:
        source_ids[asset["asset_id"]] = container_id(compose_file, asset["compose_service"])

    # attempt별 원시 관측치: (source_asset, target_asset) -> [reachable_bool, ...]
    observations: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for asset in assets:
        source_id = asset["asset_id"]
        targets = [
            (other["asset_id"], other["compose_service"], int(other["target_port"]))
            for other in assets
            if other["asset_id"] != source_id
        ]
        for _attempt in range(args.attempts):
            results = probe_once(image, source_ids[source_id], targets)
            for target_asset, rec in results.items():
                observations[(source_id, target_asset)].append(rec)

    rows = []
    unstable_pairs = []
    for (source_asset, target_asset), obs in observations.items():
        reachable_values = {o["reachable"] for o in obs}
        stable = len(reachable_values) == 1
        if not stable:
            unstable_pairs.append((source_asset, target_asset, [o["reachable"] for o in obs]))
        reachable = obs[0]["reachable"] if stable else None
        elapsed_ms = statistics.median(o["elapsed_ms"] for o in obs)
        errors = "; ".join(sorted({o["error"] for o in obs if o["error"]}))
        src = asset_by_id[source_asset]
        dst = asset_by_id[target_asset]
        rows.append({
            "mode": args.mode,
            "source_asset": source_asset,
            "target_asset": target_asset,
            "source_business": src["business"],
            "target_business": dst["business"],
            "source_tier": src["tier"],
            "target_tier": dst["tier"],
            "target_port": dst["target_port"],
            "reachable": "" if reachable is None else str(reachable).lower(),
            "attempts": len(obs),
            "stable": str(stable).lower(),
            "elapsed_ms": f"{elapsed_ms:.3f}",
            "error": errors,
        })

    output = RESULTS / f"raw_network_edges_{args.mode}_{timestamp()}.csv"
    write_csv(output, list(rows[0].keys()), rows)
    print(output)
    print(f"pairs_tested={len(rows)} (expected {len(assets) * (len(assets) - 1)})")

    if unstable_pairs:
        print(f"UNSTABLE: {len(unstable_pairs)} pair(s) gave inconsistent results across {args.attempts} attempts:", file=sys.stderr)
        for source_asset, target_asset, values in unstable_pairs:
            print(f"  {source_asset} -> {target_asset}: {values}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
