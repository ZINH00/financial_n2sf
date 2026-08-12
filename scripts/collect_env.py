from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from common import ROOT

# 재현성 확보를 위해 실험 환경의 하드웨어/소프트웨어/정책 버전을 한 번에 기록한다.
# run_all.sh가 각 모드 실행 전에 한 번 호출하며, 결과는 results/experiment_metadata.json에
# 저장되어 논문 Table 3(실험장비·소프트웨어 버전)의 근거자료로 쓸 수 있다.


def run_cmd(cmd: list[str]) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
        return result.stdout.strip() if result.returncode == 0 else f"unavailable: {result.stderr.strip()}"
    except Exception as exc:
        return f"unavailable: {type(exc).__name__}: {exc}"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def total_ram_bytes() -> int | None:
    system = platform.system()
    try:
        if system == "Windows":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
            return int(stat.ullTotalPhys)
        if system == "Linux":
            with open("/proc/meminfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) * 1024
        if system == "Darwin":
            result = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5, check=False)
            return int(result.stdout.strip())
    except Exception:
        return None
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect host/software/policy metadata for experiment reproducibility.")
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    args = parser.parse_args()
    results = args.results_dir
    results.mkdir(parents=True, exist_ok=True)

    ram_bytes = total_ram_bytes()
    policy_dir = ROOT / "policies"
    scenario_dir = ROOT / "scenarios"

    metadata = {
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": {
            "os": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor() or "unknown",
            "cpu_count_logical": os.cpu_count(),
            "ram_gib": round(ram_bytes / (1024**3), 2) if ram_bytes else None,
        },
        "software": {
            "python_version": platform.python_version(),
            "docker_engine_version": run_cmd(["docker", "version", "--format", "{{.Server.Version}}"]),
            "docker_compose_version": run_cmd(["docker", "compose", "version", "--short"]),
            "opa_image": "openpolicyagent/opa:1.4.2-static",
            "opa_image_digest": run_cmd(["docker", "image", "inspect", "openpolicyagent/opa:1.4.2-static", "--format", "{{index .RepoDigests 0}}"]),
            "postgres_image": "postgres:17-alpine",
            "postgres_image_digest": run_cmd(["docker", "image", "inspect", "postgres:17-alpine", "--format", "{{index .RepoDigests 0}}"]),
        },
        "repository": {
            "git_commit_sha": run_cmd(["git", "-C", str(ROOT), "rev-parse", "HEAD"]),
            "git_branch": run_cmd(["git", "-C", str(ROOT), "rev-parse", "--abbrev-ref", "HEAD"]),
            "git_dirty": run_cmd(["git", "-C", str(ROOT), "status", "--porcelain"]) != "",
        },
        "policy_hashes_sha256": {p.name: file_sha256(p) for p in sorted(policy_dir.glob("*.rego"))} | {"data.json": file_sha256(policy_dir / "data.json")},
        "scenario_hashes_sha256": {p.name: file_sha256(p) for p in sorted(scenario_dir.glob("*.csv"))},
    }
    output = results / "experiment_metadata.json"
    output.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
