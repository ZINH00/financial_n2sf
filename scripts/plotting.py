from __future__ import annotations

from common import MODES

# 4개 실험조건에 고정 배정한 색상·표시라벨. 그래프 지표(analyze_graph_metrics.py)와
# 성능 지표(analyze_results.py) 산출물 전체에서 동일하게 재사용해 시리즈 식별이
# 일관되도록 한다.
PALETTE = {
    "surface": "#fcfcfb",
    "text_primary": "#0b0b0b",
    "text_secondary": "#52514e",
    "muted": "#898781",
    "gridline": "#e1e0d9",
    "axis": "#c3c2b7",
    "baseline": "#2a78d6",
    "policy_only": "#eb6834",
    "segmentation_only": "#1f9e89",
    "proposed": "#d1a12a",
}

MODE_LABELS = {
    "baseline": "Baseline\n(flat + broad)",
    "policy_only": "Policy-only\n(flat + fine-grained)",
    "segmentation_only": "Segmentation-only\n(segmented + broad)",
    "proposed": "Proposed\n(segmented + fine-grained)",
}
MODE_LABELS_SHORT = {
    "baseline": "Baseline",
    "policy_only": "Policy-only",
    "segmentation_only": "Segmentation-only",
    "proposed": "Proposed",
}

assert set(MODE_LABELS) == set(MODES)


def style_axes(ax) -> None:
    ax.set_facecolor(PALETTE["surface"])
    ax.figure.set_facecolor(PALETTE["surface"])
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(PALETTE["axis"])
    ax.tick_params(colors=PALETTE["text_secondary"])
    ax.yaxis.grid(True, color=PALETTE["gridline"], linewidth=0.8)
    ax.set_axisbelow(True)
    ax.xaxis.label.set_color(PALETTE["text_primary"])
    ax.yaxis.label.set_color(PALETTE["text_primary"])
    ax.title.set_color(PALETTE["text_primary"])


def bar_labels(ax, bars, fmt) -> None:
    for bar in bars:
        height = bar.get_height()
        ax.annotate(fmt(height), (bar.get_x() + bar.get_width() / 2, height), textcoords="offset points", xytext=(0, 4), ha="center", fontsize=7.5, color=PALETTE["text_primary"])
