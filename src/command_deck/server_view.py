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
        y = 86 * (1 - point.percent / 100)
        if previous is None or point.timestamp - previous.timestamp > gap_seconds:
            # A zero-length line with round caps also makes isolated samples visible.
            segments.append(f"M{x:.1f},{y:.1f}l0,0")
        else:
            segments.append(f"L{x:.1f},{y:.1f}")
        previous = point
    return " ".join(segments)


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
    return {
        "cpu_path": chart_path(trend.cpu, trend.start, trend.end, _gap_seconds(trend)),
        "memory_path": chart_path(trend.memory, trend.start, trend.end, _gap_seconds(trend)),
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
