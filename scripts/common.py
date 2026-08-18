from __future__ import annotations

import csv
import json
import random
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
SCENARIOS = ROOT / "scenarios"

# 2x2 ablation 설계의 두 축(정책 세분화 여부 x 네트워크 분리 여부)을 4개 실험
# 모드에 매핑한다. baseline/proposed는 기존 2조건 비교와 동일하고, policy_only/
# segmentation_only가 각 통제요소를 단독으로 분리해서 검증하는 중간 조건이다.
MODE_AXES: dict[str, dict[str, str]] = {
    "baseline": {"policy": "broad", "network": "flat"},
    "policy_only": {"policy": "finegrained", "network": "flat"},
    "segmentation_only": {"policy": "broad", "network": "segmented"},
    "proposed": {"policy": "finegrained", "network": "segmented"},
}
MODES: tuple[str, ...] = ("baseline", "policy_only", "segmentation_only", "proposed")

# PEP가 실제 Rego 판단에 도달하지 못했음을 뜻하는 사유들. HTTP status만으로는
# 정책적 거부(deny)와 PEP/PDP/목적지 연결 실패가 구분되지 않으므로, 그래프 간선
# 여부나 지표 산출 시 이 사유에 해당하는 요청은 실행 오류로 취급해 제외한다
# (신원 미검증 계열 사유인 unregistered_workload/workload_signature_invalid는
# 반대로 그 자체가 검증 대상인 정책적 판단이므로 제외 대상이 아니다).
STRUCTURAL_ERROR_REASONS = {"opa_transport_error", "opa_error", "upstream_transport_error", "unknown_destination"}


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def read_csv(name: str) -> list[dict[str, str]]:
    with (SCENARIOS / name).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y"}


def to_bool_series(series):
    """CSV에서 다시 읽어온 true/false 컬럼을 안전하게 불리언으로 정규화한다.

    pandas는 소문자 true/false로만 이루어진 컬럼을 bool dtype으로 자동 추론하는데,
    이 경우 str()은 대문자 "True"/"False"를 반환하므로 "true"와의 문자열 비교가
    조용히 항상 False가 된다. dtype과 무관하게 항상 올바르게 판별하기 위한 헬퍼.
    """
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower() == "true"


def join_audit_decision(rows: list[dict], audit_rows: list[dict], id_field: str = "attempt_id") -> list[dict]:
    """요청 CSV 행을 PEP/CDS 감사로그와 (id_field, experiment_run_id)로 1:1 조인해
    실제 정책 판단(decision/reason)을 붙인다. HTTP status는 정책적 거부와
    PEP<->OPA/목적지 연결 실패를 구분하지 못하므로(예: opa_transport_error도
    비2xx), 그래프 간선 여부나 지표는 이 함수가 붙인 audit_decision을 근거로
    판단해야 한다. 감사로그에서 매칭되는 기록이 아예 없으면(요청이 PEP에 도달
    못함) 보수적으로 실행 오류로 취급한다."""
    index: dict[tuple[str, str], dict] = {}
    for row in audit_rows:
        key = (str(row.get("scenario_id", "")), str(row.get("experiment_run_id", "")))
        index[key] = row
    out = []
    for row in rows:
        key = (str(row.get(id_field, "")), str(row.get("experiment_run_id", "")))
        rec = index.get(key)
        reason = rec.get("reason") if rec else None
        decision = rec.get("decision") if rec else None
        is_execution_error = reason is None or reason in STRUCTURAL_ERROR_REASONS
        out.append({**row, "audit_decision": decision or "", "audit_reason": reason or "", "is_execution_error": str(is_execution_error).lower()})
    return out


def parse_json(value: str) -> dict:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("content_json must be a JSON object")
    return parsed


def percentile(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = (len(ordered) - 1) * p
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    fraction = index - low
    return ordered[low] * (1 - fraction) + ordered[high] * fraction


def iqr(values: list[float]) -> float:
    if len(values) < 2:
        return float("nan")
    quantiles = statistics.quantiles(values, n=4, method="inclusive")
    return quantiles[2] - quantiles[0]


def bootstrap_ci(values: list[float], n_boot: int = 2000, alpha: float = 0.05, seed: int = 7) -> tuple[float, float]:
    """중앙값에 대한 백분위수 부트스트랩 95% 신뢰구간.

    반복 측정을 독립 표본으로 취급하기보다, 독립적으로 재기동된 라운드별 대표값
    (예: 라운드 내 4개 업무흐름의 median) 목록을 입력으로 받아 그 목록을
    리샘플링하는 용도로 사용한다.
    """
    if not values:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    n = len(values)
    boot_medians = [statistics.median(values[rng.randrange(n)] for _ in range(n)) for _ in range(n_boot)]
    boot_medians.sort()
    lower_idx = max(0, int((alpha / 2) * n_boot))
    upper_idx = min(n_boot - 1, int((1 - alpha / 2) * n_boot))
    return (boot_medians[lower_idx], boot_medians[upper_idx])
