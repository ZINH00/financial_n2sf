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

    반복 측정을 독립 표본으로 취급하기보다, 배치별 요약값(예: 배치 median) 목록을
    입력으로 받아 그 목록을 리샘플링하는 용도로 사용한다.
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
