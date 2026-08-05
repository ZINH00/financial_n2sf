#!/usr/bin/env bash
set -euo pipefail
MODE="${1:-}"
if [[ "$MODE" != "baseline" && "$MODE" != "proposed" ]]; then
  echo "usage: $0 baseline|proposed" >&2
  exit 2
fi
python scripts/run_policy_tests.py --mode "$MODE" --repeat 30
python scripts/run_cds_tests.py --mode "$MODE" --repeat 30
python scripts/run_reachability_tests.py --mode "$MODE"
python scripts/run_performance_tests.py --mode "$MODE" --requests 1000 --warmup 50
