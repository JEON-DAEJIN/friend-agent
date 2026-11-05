"""약속·일정을 물으면 저장된 약속을 보고 답한다(N057 실습).
'일주일 내 나의 일정을 한번 알려줘'에 모델이 조회 도구를 안 고르고(tools: []) 대화 기억만으로 답해, 저장된 '물 마시기'가 빠졌다.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import router

NOW = datetime(2026, 11, 5, 15, 0, tzinfo=ZoneInfo("Asia/Seoul"))


class Model:
    can_reason = True
    model = "fixed"

    def __init__(self, tools):
        self.tools = tools

    def complete_json(self, system, user, usage=None):
        return {"tools": self.tools, "confidence": 1.0, "reason": "x"}


@pytest.mark.parametrize("message", [
    "일주일 내 나의 일정을 한번 알려줘",          # 실습에서 조회 없이 답했던 말
    "내 약속 뭐 있어?",
    "이번 주 일정 보여 줘",
    "알림 맞춰 둔 거 확인해 줘",
])
def test_schedule_question_gets_a_lookup_even_if_the_model_chose_nothing(message):
    choice = router.choose(message, [], NOW, "서울", Model([]))
    assert [t["name"] for t in choice.tools] == ["list_reminders"]
    assert any("조회 도구를 고르지 않아" in g for g in choice.guards)


def test_other_tools_are_kept_and_lookup_is_added():
    weather = [{"name": "get_weather", "args": {"location": "서울", "date": "2026-11-06"}}]
    choice = router.choose("내일 날씨랑 내 일정 알려 줘", [], NOW, "서울", Model(weather))
    assert [t["name"] for t in choice.tools] == ["get_weather", "list_reminders"]


@pytest.mark.parametrize("message, tools", [
    ("11월 7일 오전 10시 30분 미용실 약속 알려 줘",
     [{"name": "create_reminder", "args": {"text": "미용실", "due_at": "2026-11-07T10:30:00+09:00"}}]),  # 저장 부탁은 저장만
    ("미용실 약속 취소해 줘", [{"name": "cancel_reminder", "args": {"query": "미용실"}}]),
])
def test_save_or_cancel_is_not_mixed_with_a_lookup(message, tools):
    choice = router.choose(message, [], NOW, "서울", Model(tools))
    assert "list_reminders" not in [t["name"] for t in choice.tools]


@pytest.mark.parametrize("message", ["나 다음 주 토요일에 이사 가", "오늘 너무 피곤해", "고양이 이름 뭐로 지을까?"])
def test_ordinary_talk_gets_no_lookup(message):
    choice = router.choose(message, [], NOW, "서울", Model([]))
    assert choice.tools == []
