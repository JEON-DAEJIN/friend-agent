"""평가 데이터와 평가 스크립트의 약속(N055)을 지킨다.

- T26·T28 의 정답은 특정 기간이 아니라 '조회 기간이 날짜를 포함하는가'다. 인자를 비워 1년을 찾아도 정답이어야 한다.
- 데이터셋 항목 id 는 '데이터셋-원본id'로 고정돼서 다시 올려도 항목이 늘지 않는다.
- holiday-reply 는 실패 사례와 정상 사례를 함께 가지고, 항목마다 도구 결과 문장과 통과 조건이 있다.
실제 LLM 이나 Langfuse 는 부르지 않는다.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import eval_tools  # noqa: E402
import register_holiday_reply_prompt as reg  # noqa: E402
import upload_datasets as up  # noqa: E402


def test_next_holiday_is_right_when_the_range_contains_it_even_if_args_are_empty():
    assert eval_tools.args_match({"next_holiday": "2026-12-25"}, {}) == []                    # 인자를 비우면 오늘부터 1년
    assert eval_tools.args_match({"next_holiday": "2026-12-25"}, {"from": "2026-12-01", "to": "2026-12-31"}) == []


def test_next_holiday_is_wrong_when_the_range_is_too_short():
    wrong = eval_tools.args_match({"next_holiday": "2026-12-25"}, {"from": "2026-10-12", "to": "2026-10-31"})
    assert len(wrong) == 1 and "2026-12-25" in wrong[0]


def test_covers_needs_the_whole_range():
    want = {"covers": ["2026-12-01", "2026-12-31"]}
    assert eval_tools.args_match(want, {}) == []
    assert eval_tools.args_match(want, {"from": "2026-12-01", "to": "2026-12-31"}) == []
    assert len(eval_tools.args_match(want, {"from": "2026-12-05", "to": "2026-12-31"})) == 1


def test_t26_and_t28_in_the_data_use_the_range_check():
    rows = {r["id"]: r for r in up.read_jsonl("tool_selection.jsonl")}
    assert rows["T26"]["expected_args"] == {"get_holidays": {"next_holiday": "2026-12-25"}}
    assert rows["T28"]["expected_args"] == {"get_holidays": {"covers": ["2026-12-01", "2026-12-31"]}}


def test_dataset_sizes_and_item_ids_are_stable_and_unique():
    sizes = {}
    for name, file_name, builder, _ in up.DATASETS:
        items = up.build_items(name, file_name, builder)
        ids = [i[0] for i in items]
        assert len(ids) == len(set(ids)), name
        assert all(i.startswith(name + "-") for i in ids)
        assert ids == [i[0] for i in up.build_items(name, file_name, builder)]   # 다시 만들어도 같다
        sizes[name] = len(items)
    assert sizes["tool-selection"] == 41 and sizes["memory-extraction"] == 15 and sizes["safety"] == 15
    assert sizes["holiday-reply"] >= 10


def test_holiday_reply_has_failure_and_normal_cases_with_fixed_tool_results():
    rows = up.read_jsonl("holiday_reply.jsonl")
    assert {r["kind"] for r in rows} == {"failure", "normal"}
    for r in rows:
        assert r["tool_result"].strip() and r["pass_condition"].strip() and r["message"].strip()
        assert "{{" not in r["tool_result"]
    built = up.build_holiday_reply(rows[0])
    assert set(built["input"]) == {"message", "tool_result"}          # 프롬프트 변수 이름과 같다
    assert built["expected_output"] == rows[0]["pass_condition"]
    assert built["metadata"]["kind"] in ("failure", "normal")


def test_answer_prompt_has_exactly_two_variables_and_the_fixed_time():
    system, user = reg.build_messages()
    assert system["content"].count("{{tool_result}}") == 1
    assert user["content"] == "{{message}}"
    assert "[지금] 11월 2일(월) 14:00" in system["content"]
    assert "이름: 하루" in system["content"]
