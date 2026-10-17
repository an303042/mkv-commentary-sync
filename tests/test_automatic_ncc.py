import numpy as np
import pytest

from core.detect_offset import (
    OffsetSample,
    _correlation_metrics,
    _select_automatic_consensus,
)


def _sample(start, offset, ncc=0.05, prominence=0.5):
    return OffsetSample(
        start=float(start),
        label=f"00:00:{start:02d}",
        offset_ms=offset,
        ncc=ncc,
        prominence=prominence,
    )


def test_correlation_metrics_distinguish_clear_random_peak():
    rng = np.random.default_rng(1234)
    audio = rng.normal(size=4096)

    result = _correlation_metrics(audio, audio, peak_exclusion_samples=32)

    assert result.lag_samples == 0
    assert result.peak_ncc == pytest.approx(1.0)
    assert result.peak_prominence > 0.9


def test_correlation_metrics_report_silence_as_no_match():
    silence = np.zeros(1024)

    result = _correlation_metrics(silence, silence, peak_exclusion_samples=16)

    assert result.peak_ncc == 0.0
    assert result.peak_prominence == 0.0


def test_automatic_consensus_discards_weak_outlier():
    readings = [
        _sample(100, 1000),
        _sample(200, 1002),
        _sample(300, 998),
        _sample(400, 90000, ncc=0.03, prominence=0.02),
    ]

    selected = _select_automatic_consensus(readings)

    assert [sample.offset_ms for sample in selected] == [1000, 1002, 998]


def test_automatic_consensus_rejects_strong_contradiction():
    readings = [
        _sample(100, 1000),
        _sample(200, 1002),
        _sample(300, 998),
        _sample(400, 90000, ncc=0.30, prominence=0.50),
    ]

    with pytest.raises(RuntimeError, match="Strong correlation readings contradict"):
        _select_automatic_consensus(readings)


def _terminator_readings(point4_offset):
    # Real readings from RLM 7.1 remaster vs Tigole RM4K 5.1.
    return [
        _sample(480, -16062, ncc=0.214, prominence=0.67),
        _sample(1605, -16030, ncc=0.344, prominence=0.90),
        _sample(2569, -16069, ncc=0.132, prominence=0.56),
        _sample(3854, point4_offset, ncc=0.213, prominence=0.89),
        _sample(4817, -16067, ncc=0.243, prominence=0.94),
    ]


def test_automatic_consensus_tolerates_isolated_strong_blip():
    warnings = []

    selected = _select_automatic_consensus(
        _terminator_readings(-16148), warn=warnings.append
    )

    assert [sample.offset_ms for sample in selected] == [-16062, -16030, -16069, -16067]
    assert len(warnings) == 1 and "-16148" in warnings[0]


def test_automatic_consensus_rejects_large_bracketed_conflict():
    with pytest.raises(RuntimeError, match="Strong correlation readings contradict"):
        _select_automatic_consensus(_terminator_readings(-18100))


def test_automatic_consensus_rejects_strong_conflict_at_last_point():
    readings = _terminator_readings(-16070)
    readings[-1] = _sample(4817, -16150, ncc=0.243, prominence=0.94)

    with pytest.raises(RuntimeError, match="Strong correlation readings contradict"):
        _select_automatic_consensus(readings)


def test_automatic_consensus_requires_three_agreeing_offsets():
    readings = [
        _sample(0, 0),
        _sample(100, 10000),
        _sample(200, -10000),
    ]

    with pytest.raises(RuntimeError, match="could not find three consistent"):
        _select_automatic_consensus(readings)
