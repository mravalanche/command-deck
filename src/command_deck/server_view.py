"""Small, server-rendered trend chart geometry."""

from command_deck.collectors.trends import HostTrend, TrendPoint


def chart_path(points: list[TrendPoint], start: float, end: float) -> str:
    """Create a fixed-size SVG path; gaps remain visible rather than bridged."""
    if end <= start:
        return ""
    segments: list[str] = []
    previous: TrendPoint | None = None
    for point in points:
        if not start <= point.timestamp <= end:
            continue
        x = 300 * (point.timestamp - start) / (end - start)
        y = 86 * (1 - point.percent / 100)
        action = "M" if previous is None or point.timestamp - previous.timestamp > 30 else "L"
        segments.append(f"{action}{x:.1f},{y:.1f}")
        previous = point
    return " ".join(segments)


def trend_note(trend: HostTrend) -> str:
    if not trend.has_data:
        return trend.reason or "Trend unavailable"
    if trend.reason:
        return trend.reason
    oldest = min(
        (points[0].timestamp for points in (trend.cpu, trend.memory) if points),
        default=trend.end,
    )
    if oldest - trend.start > 45:
        return "Partial five-minute history"
    return "Past 5 min"
