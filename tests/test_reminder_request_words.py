"""약속을 부탁하는 말 목록(N057 실습). '새 약속 저장해 줘: 11월 7일 …'에서 모델은 맞게 골랐는데
코드 안전장치가 '알려 달라는 부탁이 없다'며 약속을 뺐다. 저장·등록 부탁은 통과하고, 그냥 일정 이야기는 여전히 막혀야 한다.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import router

NOW = datetime(2026, 11, 5, 14, 48, tzinfo=ZoneInfo("Asia/Seoul"))


class PickReminder:
    """무조건 약속 저장을 고르는 모델 흉내."""
    can_reason = True
    model = "pick"

    def complete_json(self, system, user, usage=None):
        return {"tools": [{"name": "create_reminder", "args": {"text": "미용실", "due_at": "2026-11-07T10:30:00+09:00"}}],
                "confidence": 0.9, "reason": "약속을 만든다"}


@pytest.mark.parametrize("message", [
    "새 약속 저장해 줘: 11월 7일 오전 10시 30분 미용실",     # 실습에서 빠졌던 말
    "11월 7일 오전 10시 30분 미용실 예약 저장해줘",
    "11월 7일 오전 10시 30분 미용실 등록해 줘",
    "11월 7일 오전 10시 30분 미용실 약속으로 잡아 줘",
    "11월 7일 오전 10시 30분에 미용실 약속 잡아 줘",
    "11월 7일 오전 10시 30분 미용실 예약 알려 줘",            # 원래부터 통과하던 말
])
def test_asking_to_save_keeps_the_reminder(message):
    choice = router.choose(message, [], NOW, "서울", PickReminder())
    assert [t["name"] for t in choice.tools] == ["create_reminder"]
    assert choice.tools[0]["args"]["due_at"].startswith("2026-11-07T10:30")


@pytest.mark.parametrize("message", [
    "나 다음 주 토요일에 이사 가",
    "11월 7일에 미용실 다녀올 거야",
    "어제 사진 저장했어",                                     # '저장'만 있고 부탁이 아니다
])
def test_just_talking_about_plans_still_removes_it(message):
    choice = router.choose(message, [], NOW, "서울", PickReminder())
    assert choice.tools == []
