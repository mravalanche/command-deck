"""Chart geometry and labels describe retained samples without inventing history."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from command_deck.collectors.trends import HostTrend, TrendPoint
from command_deck.models import DiskView, HostView, Telemetry
from command_deck.server_view import chart_context, chart_path


def trend(*, window=600, interval=10, cpu=None, memory=None, reason=None):
    points = [TrendPoint(timestamp, 40) for timestamp in range(0, window + 1, interval)]
    return HostTrend(
        telemetry=Telemetry.CONNECTED, start=0, end=window,
        cpu=points if cpu is None else cpu, memory=points if memory is None else memory,
        interval_seconds=interval, reason=reason,
    )


@pytest.mark.parametrize(('window', 'labels'), [
    (300, ['−5', '−4', '−3', '−2', '−1', 'Now']),
    (600, ['−10', '−8', '−6', '−4', '−2', 'Now']),
    (330, ['−5.5', '−4.4', '−3.3', '−2.2', '−1.1', 'Now']),
    (3600, ['−60', '−48', '−36', '−24', '−12', 'Now']),
])
def test_axis_and_window_labels_follow_configured_duration(window, labels):
    context = chart_context(trend(window=window))
    assert context['history_axis_labels'] == labels
    assert context['history_window_description'] == f'{window / 60:g} minutes'
    assert context['history_interval_seconds'] == '10'
    assert context['trend_note'].startswith('Past ')
    assert context['cpu_partial'] is False


def test_default_window_plots_every_sample_and_summarizes_observed_values():
    value = trend(cpu=[TrendPoint(index * 10, index) for index in range(61)])
    context = chart_context(value)
    assert context['cpu_sample_count'] == 61
    assert context['cpu_peak_percent'] == 60
    assert context['cpu_average_percent'] == 30
    assert context['cpu_path'].startswith('M0.0,86.0l0,0 L5.0,85.1')
    assert context['cpu_path'].endswith('L300.0,34.4')
    assert context['trend_note'] == 'Past 10 min · 10s polling'


def test_warmup_and_missing_series_are_partial_with_no_invented_samples():
    context = chart_context(trend(cpu=[TrendPoint(600, 18)], memory=[]))
    assert context['cpu_path'] == 'M300.0,70.5l0,0'
    assert context['memory_path'] == ''
    assert context['cpu_sample_count'] == 1
    assert context['cpu_partial'] is True
    assert context['trend_note'] == 'Partial 10 min · 10s polling'
    assert chart_context(trend(cpu=[], memory=[]))['trend_note'] == 'Waiting for history samples'


def test_collection_gaps_break_paths_and_mark_summary_partial():
    context = chart_context(trend(cpu=[TrendPoint(0, 40), TrendPoint(10, 60), TrendPoint(600, 20)]))
    assert context['cpu_path'].count('M') == 2
    assert 'M300.0,68.8l0,0' in context['cpu_path']
    assert context['cpu_partial'] is True
    # At a slower configured interval, adjacent real samples still form a line.
    context = chart_context(trend(interval=30))
    assert context['cpu_path'].count('M') == 1
    assert context['cpu_partial'] is False


def test_chart_filters_out_of_window_samples_and_handles_zero_duration():
    points = [TrendPoint(-1, 20), TrendPoint(0, 40), TrendPoint(300, 60), TrendPoint(301, 80)]
    assert chart_path(points, 0, 300) == 'M0.0,51.6l0,0 M300.0,34.4l0,0'
    assert chart_path(points, 0, 0) == ''


def test_disconnect_keeps_history_visible_without_stale_current_values():
    templates = Path(__file__).parents[1] / 'src' / 'command_deck' / 'templates'
    env = Environment(loader=FileSystemLoader(templates), autoescape=select_autoescape(['html']))
    selected = HostView(
        id='host', name='Host', telemetry=Telemetry.DISCONNECTED,
        metrics_current=False, cpu_percent=99, memory_percent=98,
        disks=[DiskView(label='Stale drive', mountpoint='/', used_percent=77)],
        updated_at=datetime.now(UTC),
    )
    value = trend(reason='Collector is unavailable')
    html = env.get_template('partials/servers.html').render(
        hosts=[selected], selected=selected, trend=value, **chart_context(value),
    )
    assert 'Current telemetry unavailable. Recent history is shown below.' in html
    assert 'class="chart-cpu"' in html and 'class="chart-memory"' in html
    assert '>99%' not in html and '>98%' not in html
    assert 'Stale drive' not in html
    assert 'Storage unavailable' in html
    assert 'Resource history over 10 minutes' in html
    assert 'Collection interval 10 seconds' in html
    assert 'hx-trigger="every 10s, refresh"' in html
    assert '<span>−10</span><span>−8</span>' in html
    assert 'Collector is unavailable' in html


def test_sparse_coverage_does_not_claim_full_configured_resolution():
    sparse = [TrendPoint(timestamp, 40) for timestamp in range(0, 601, 30)]
    context = chart_context(trend(cpu=sparse, memory=sparse))
    assert context['cpu_partial'] is True
    assert context['trend_note'] == 'Partial 10 min · 10s polling'
    assert context['cpu_sample_count'] == 21
