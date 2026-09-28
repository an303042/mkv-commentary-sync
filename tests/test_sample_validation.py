import pytest

from core.detect_offset import _require_reliable_sample_count


@pytest.mark.parametrize("passed", [0, 1, 2])
def test_too_few_reliable_samples_are_rejected(passed):
    with pytest.raises(RuntimeError, match="need at least 3"):
        _require_reliable_sample_count(passed, attempted=5)


def test_three_reliable_samples_are_accepted():
    _require_reliable_sample_count(3, attempted=5)
