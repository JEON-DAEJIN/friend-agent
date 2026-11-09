"""'11월 7일'처럼 달력 날짜로 말한 약속(N057 실습). 정답은 코드가 계산한다.

지금은 2026-11-05(목) 14:48. 이 시각에 오전 10:30 은 이미 지났다.
예전에는 달력 날짜를 몰라 '오늘 10:30 → 지났으니 내일' 로 읽어 11/6 이 됐고,
fix_reminder_date 가 모델의 맞는 날짜를 그 틀린 날짜로 덮어썼다.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import router, when

NOW = datetime(2026, 11, 5, 14, 48, tzinfo=ZoneInfo("Asia/Seoul"))
OCR = "사진에서 읽은 글이야. 여기 있는 일정 중에 알려 줘야 할 게 있으면 약속으로 저장해 줘.\n제목\n11월 7일\n오전 10시30분\n미용실 예약"


@pytest.mark.parametrize("text, expected", [
    (OCR, "2026-11-07T10:30"),                                   # 실습에서 11/6 으로 저장됐던 글
    ("11월 7일 오전 10시 30분 미용실 예약", "2026-11-07T10:30"),
    ("11월7일 오후 3시 치과", "2026-11-07T15:00"),
    ("11월 20일 오후 3시 치과 알려 줘", "2026-11-20T15:00"),
    ("12월 3일 저녁에 모임", "2026-12-03T19:00"),
    ("1월 2일 아침 9시 출근", "2027-01-02T09:00"),                # 이미 지난 달이면 내년
    ("11월 7일 전날 밤에 알려 줘", "2026-11-06T21:00"),           # 달력 날짜도 '전날'의 기준이 된다
])
def test_calendar_date_is_read(text, expected):
    assert when.parse_when(text, NOW).strftime("%Y-%m-%dT%H:%M") == expected


def test_impossible_date_is_ignored():
    assert when.month_day_offset("2월 30일 오후 3시", NOW) is None


def test_model_date_is_kept_when_it_matches_the_calendar_date():
    args = {"due_at": "2026-11-07T10:30:00+09:00"}
    assert router.fix_reminder_date(OCR, args, NOW) is None       # 예전에는 (11/7, 11/6) 으로 '고쳤다'
    assert args["due_at"] == "2026-11-07T10:30:00+09:00"
