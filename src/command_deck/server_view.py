"""Small, server-rendered trend chart geometry and shared presentation values."""

from command_deck.collectors.trends import HostTrend, TrendPoint


def chart_path(
    points: list[TrendPoint], start: float, end: float, gap_seconds: float = 30,
) -> str:
    """Draw observed samples; start a new segment after a collection gap."""
    if end <= start:
        return ""
    segments: list[str] = []
    previous: TrendPoint | None = None
    for point in points:
        if not start <= point.timestamp <= end:
            continue
        x = 300 * (point.timestamp - start) / (end - start)
        y = 2 + 82 * (1 - point.percent / 100)
        if previous is None or point.timestamp - previous.timestamp > gap_seconds:
            # A zero-length line with round caps also makes isolated samples visible.
            segments.append(f"M{x:.1f},{y:.1f}l0,0")
        else:
            segments.append(f"L{x:.1f},{y:.1f}")
        previous = point
    return " ".join(segments)


def _bridge_path(points: list[TrendPoint], trend: HostTrend) -> str:
    """Connect nearby observations with an explicit missing-reading style."""
    visible = [point for point in points if trend.start <= point.timestamp <= trend.end]
    segments = []
    for earlier, later in zip(visible, visible[1:], strict=False):
        if _gap_seconds(trend) < later.timestamp - earlier.timestamp <= 90:
            x1 = 300 * (earlier.timestamp - trend.start) / (trend.end - trend.start)
            x2 = 300 * (later.timestamp - trend.start) / (trend.end - trend.start)
            y1 = 2 + 82 * (1 - earlier.percent / 100)
            y2 = 2 + 82 * (1 - later.percent / 100)
            segments.append(f"M{x1:.1f},{y1:.1f} L{x2:.1f},{y2:.1f}")
    return " ".join(segments)


def _outage_bands(trend: HostTrend) -> list[dict[str, float]]:
    """Mark long periods with neither series observed; startup is not an outage."""
    stamps = sorted({point.timestamp for points in (trend.cpu, trend.memory)
                     for point in points if trend.start <= point.timestamp <= trend.end})
    if not stamps or trend.end <= trend.start:
        return []
    bands = []
    for earlier, later in zip(stamps, [*stamps[1:], trend.end], strict=True):
        if later - earlier > 90:
            bands.append({
                "x": 300 * (earlier - trend.start) / (trend.end - trend.start),
                "width": 300 * (later - earlier) / (trend.end - trend.start),
            })
    return bands


def _gap_seconds(trend: HostTrend) -> float:
    return max(30, 2.5 * trend.interval_seconds)


def _partial(points: list[TrendPoint], trend: HostTrend) -> bool:
    if not points:
        return True
    gap = _gap_seconds(trend)
    return (
        len(points) < 0.8 * ((trend.end - trend.start) / trend.interval_seconds + 1)
        or points[0].timestamp > trend.start + gap
        or points[-1].timestamp < trend.end - gap
        or any(
            later.timestamp - earlier.timestamp > gap
            for earlier, later in zip(points, points[1:], strict=False)
        )
    )


def trend_note(trend: HostTrend) -> str:
    if not trend.has_data:
        return trend.reason or "Waiting for history samples"
    if trend.reason:
        return trend.reason
    window = (trend.end - trend.start) / 60
    prefix = "Partial" if _partial(trend.cpu, trend) or _partial(trend.memory, trend) else "Past"
    return f"{prefix} {window:g} min · {trend.interval_seconds:g}s polling"


def chart_context(trend: HostTrend) -> dict:
    """Keep chart labels, summary and timing consistent for any configured window."""
    duration = trend.end - trend.start
    minutes = duration / 60
    cpu = [point.percent for point in trend.cpu]
    cpu_bridges = _bridge_path(trend.cpu, trend)
    memory_bridges = _bridge_path(trend.memory, trend)
    outage_bands = _outage_bands(trend)
    return {
        "cpu_path": chart_path(trend.cpu, trend.start, trend.end, _gap_seconds(trend)),
        "memory_path": chart_path(trend.memory, trend.start, trend.end, _gap_seconds(trend)),
        "cpu_gap_path": cpu_bridges,
        "memory_gap_path": memory_bridges,
        "history_outage_bands": outage_bands,
        "history_has_bridges": bool(cpu_bridges or memory_bridges),
        "cpu_peak_percent": max(cpu, default=None),
        "cpu_average_percent": sum(cpu) / len(cpu) if cpu else None,
        "cpu_sample_count": len(cpu),
        "cpu_partial": _partial(trend.cpu, trend),
        "trend_note": trend_note(trend),
        "history_window_label": f"{minutes:g} min",
        "history_window_description": f"{minutes:g} minutes",
        "history_axis_labels": [
            f"−{minutes * fraction / 5:g}" for fraction in range(5, 0, -1)
        ] + ["Now"],
        "history_axis_unit": "Minutes ago",
        "history_interval_seconds": f"{trend.interval_seconds:g}",
    }
