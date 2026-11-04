"""CI 평가 게이트의 판정(scripts/eval_tools.py gate, N055 6장). 실제 LLM 은 부르지 않는다.

- 모든 회차가 기준 이상이어야 통과다. 한 회차라도 못 미치면 실패다.
- 꼭 맞아야 하는 항목이 한 회차라도 틀리면 실패다.
- 기준을 주지 않으면 판정할 것이 없다(항상 통과).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import eval_tools  # noqa: E402


def run(args_ok, passed=()):
    return {"args_ok": args_ok, "passed": set(passed)}


def test_all_runs_at_or_above_the_threshold_pass():
    ok, reasons = eval_tools.gate([run(38), run(36), run(33)], min_exact=33)
    assert ok and reasons == []


def test_one_run_below_the_threshold_fails_even_if_the_others_are_fine():
    ok, reasons = eval_tools.gate([run(38), run(32)], min_exact=33)
    assert not ok and len(reasons) == 1 and "2회차" in reasons[0] and "32개" in reasons[0]


def test_a_must_pass_item_wrong_in_any_run_fails():
    ok, reasons = eval_tools.gate([run(38, {"T34"}), run(38, set())], must_pass=["T34"])
    assert not ok and "T34" in reasons[0] and "2회차" in reasons[0]


def test_both_conditions_report_every_reason():
    ok, reasons = eval_tools.gate([run(20, set())], min_exact=33, must_pass=["T34", "T26"])
    assert not ok and len(reasons) == 3


def test_without_any_criteria_there_is_nothing_to_judge():
    assert eval_tools.gate([run(0)]) == (True, [])


def test_the_threshold_boundary_is_inclusive():
    assert eval_tools.gate([run(33)], min_exact=33)[0] and not eval_tools.gate([run(32)], min_exact=33)[0]


def test_the_must_pass_items_exist_in_the_data():
    ids = {row["id"] for row in eval_tools.load_rows()}
    assert {"T34", "T26", "T28"} <= ids
