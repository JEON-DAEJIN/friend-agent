"""'시험 전날', '이틀 전'처럼 기준일에서 앞날을 말하는 표현(N055 4장 T34). 정답은 코드가 계산한다.

지금은 2026-10-12(월) 14:00. 이 시각 기준 금요일은 10/16이다.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import router, when

NOW = datetime(2026, 10, 12, 14, 0, tzinfo=ZoneInfo("Asia/Seoul"))


@pytest.mark.parametrize("text, expected", [
    ("금요일 시험 전날 밤에 일찍 자라고 알려 줘", "2026-10-15T21:00"),     # T34: 금요일(10/16)의 전날
    ("금요일 하루 전 아침에 알려 줘", "2026-10-15T08:00"),
    ("금요일 이틀 전 저녁 7시에 알려 줘", "2026-10-14T19:00"),
    ("금요일 사흘 전 아침에 알려 줘", "2026-10-13T08:00"),
    ("금요일 3일 전 아침에 알려 줘", "2026-10-13T08:00"),
    ("다음 주 월요일 이틀 전 저녁 7시에 알려 줘", "2026-10-17T19:00"),   # 다음 주 월요일은 10/19
    ("모레 시험 전날 밤에 알려 줘", "2026-10-13T21:00"),                  # 모레는 10/14
])
def test_day_before_is_counted_from_the_named_day(text, expected):
    assert when.parse_when(text, NOW).strftime("%Y-%m-%dT%H:%M") == expected


@pytest.mark.parametrize("text", [
    "시험 전날 밤에 일찍 자라고 알려 줘",       # 기준이 되는 날을 모른다
    "5일 전에 알려 줘",
    "오늘 하루 전에 알려 줘",                   # 빼면 이미 지난 날
])
def test_without_a_known_day_or_in_the_past_it_is_not_read(text):
    assert when.parse_when(text, NOW) is None


@pytest.mark.parametrize("text, expected", [
    ("내일 아침 8시에 물 마시라고 알려 줘", "2026-10-13T08:00"),
    ("목요일 저녁 7시에 엄마한테 전화하라고 알려 줘", "2026-10-15T19:00"),
    ("다음 주 월요일 아침에 알려 줘", "2026-10-19T08:00"),
    ("30분 뒤에 알려 줘", "2026-10-12T14:30"),
])
def test_existing_expressions_are_unchanged(text, expected):
    assert when.parse_when(text, NOW).strftime("%Y-%m-%dT%H:%M") == expected


def test_guard_fixes_the_model_date_for_the_day_before_whether_it_was_off_or_right():
    message = "금요일 시험 전날 밤에 일찍 자라고 알려 줘"
    off = {"due_at": "2026-10-14T21:00:00+09:00"}          # 모델이 이틀 앞으로 계산
    assert router.fix_reminder_date(message, off, NOW) is not None and off["due_at"].startswith("2026-10-15T21:00")
    exam_day = {"due_at": "2026-10-16T21:00:00+09:00"}     # 모델이 전날을 못 읽고 시험 당일로 정함
    assert router.fix_reminder_date(message, exam_day, NOW) is not None and exam_day["due_at"].startswith("2026-10-15T21:00")
    right = {"due_at": "2026-10-15T21:00:00+09:00"}        # 모델이 맞혔으면 건드리지 않는다
    assert router.fix_reminder_date(message, right, NOW) is None and right["due_at"].startswith("2026-10-15T21:00")


def test_guard_leaves_the_model_date_alone_when_the_base_day_is_unknown():
    args = {"due_at": "2026-10-14T21:00:00+09:00"}
    assert router.fix_reminder_date("시험 전날 밤에 일찍 자라고 알려 줘", args, NOW) is None
    assert args["due_at"].startswith("2026-10-14T21:00")


def test_keyword_rule_also_reads_the_day_before():
    choice = router.rule_choose("금요일 시험 전날 밤에 일찍 자라고 알려 줘", NOW, "서울")
    assert choice.tools[0]["name"] == "create_reminder" and choice.tools[0]["args"]["due_at"].startswith("2026-10-15T21:00")
