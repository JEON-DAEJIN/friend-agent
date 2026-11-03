"""A/B 테스트 표본 수·p값 계산(scripts/ab_sample_size.py, N055 5장)이 교안의 숫자와 맞는다."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import ab_sample_size as s  # noqa: E402


def test_required_n_matches_the_lecture_number_for_20_to_15_percent():
    assert 900 <= s.required_n(0.20, 0.15) <= 905            # 교안: 그룹마다 900건 남짓
    assert s.required_n(0.20, 0.10) < s.required_n(0.20, 0.15)   # 차이가 클수록 적게 든다


def test_days_needed_matches_the_lecture_example_and_grows_when_b_gets_less_traffic():
    assert round(s.days_needed(900, 50, 0.10, 0.5)) == 360     # 교안: 900 ÷ 2.5 = 360일
    assert s.days_needed(900, 50, 0.10, 0.1) > 4 * s.days_needed(900, 50, 0.10, 0.5)


def test_p_value_is_large_for_small_samples_and_small_for_large_ones():
    assert s.p_value(13, 60, 8, 54) > 0.05
    assert s.p_value(130, 600, 81, 540) < 0.05
    assert s.p_value(10, 100, 10, 100) == 1.0
    assert abs(s.p_value(13, 60, 8, 54) - s.p_value(8, 54, 13, 60)) < 1e-12
