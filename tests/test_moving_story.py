"""'나 다음 주 토요일에 이사 가' 에서 원치 않은 약속이 저장되고 기억에 요일이 안 맞는 날짜가 들어간 일(N054 7장 실습)의 재발 방지.

기존 테스트(test_no_reminder_without_request.py)는 가짜 LLM(키워드 규칙)만 지나서, 진짜 모델이 지시문을 어기는 경로를 못 잡았다.
여기서는 '모델이 잘못 고른 것처럼 답하는 가짜 모델'을 써서 코드 안전장치가 그 경로를 막는지 확인한다.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from app import memory, router
from app.store.memory_store import MemoryStore

NOW = datetime(2026, 11, 2, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))  # 월요일. 다음 주 토요일은 11월 14일


class WrongModel:
    """지시문을 어기고 무조건 약속 저장을 고르는 모델 흉내."""
    can_reason = True
    model = "wrong"

    def __init__(self, plan):
        self.plan = plan

    def complete_json(self, system, user, usage=None):
        return self.plan


REMINDER_PLAN = {"tools": [{"name": "create_reminder", "args": {"text": "이사 가기", "due_at": "2026-11-07T09:00:00+09:00"}}],
                 "confidence": 0.9, "reason": "알림을 만든다"}


def test_model_picked_reminder_is_removed_when_the_user_did_not_ask_for_one():
    choice = router.choose("나 다음 주 토요일에 이사 가", [], NOW, "서울", WrongModel(REMINDER_PLAN))
    assert choice.tools == []
    assert any("부탁이 없는데" in g for g in choice.guards)


def test_reminder_is_kept_when_the_user_asked_to_be_told():
    choice = router.choose("다음 주 토요일 아침에 이사 준비하라고 알려 줘", [], NOW, "서울", WrongModel(REMINDER_PLAN))
    assert [t["name"] for t in choice.tools] == ["create_reminder"]


def test_weekday_mismatch_catches_the_invented_date():
    assert memory.weekday_mismatch("11월 12일(토) 이사", NOW)        # 2026-11-12 는 목요일
    assert not memory.weekday_mismatch("11월 14일(토) 이사", NOW)    # 맞는 날짜
    assert not memory.weekday_mismatch("다음 주 토요일에 이사", NOW)  # 날짜가 없으면 검사하지 않는다


def test_memory_with_wrong_weekday_is_not_saved_but_a_correct_one_is():
    store = MemoryStore()
    # 사용자 말에 상대 날짜 표현이 없을 때(날짜 고치기가 일하지 않을 때)는 요일 검사가 막는다
    wrong = WrongModel({"add": [{"kind": "event", "text": "11월 12일(토) 이사"}]})
    assert memory.update_memories(store, "u1", "이사 갈 예정이야", "그렇구나", NOW, wrong)["added"] == []
    right = WrongModel({"add": [{"kind": "event", "text": "11월 14일(토) 이사"}]})
    assert [m["text"] for m in memory.update_memories(store, "u1", "이사 갈 예정이야", "그렇구나", NOW, right)["added"]] == ["11월 14일(토) 이사"]


def test_reminder_date_is_fixed_when_it_disagrees_with_the_date_words_in_the_message():
    wrong = {"tools": [{"name": "create_reminder", "args": {"text": "이사 준비", "due_at": "2026-11-07T09:00:00+09:00"}}],
             "confidence": 0.9, "reason": "x"}
    choice = router.choose("다음 주 토요일 아침에 이사 준비하라고 알려 줘", [], NOW, "서울", WrongModel(wrong))
    assert choice.tools[0]["args"]["due_at"].startswith("2026-11-14T09:00")  # 날짜만 고치고 시각은 모델 것을 둔다
    assert any("고쳤습니다" in g for g in choice.guards)


def test_correct_reminder_date_is_left_alone():
    right = {"tools": [{"name": "create_reminder", "args": {"text": "이사 준비", "due_at": "2026-11-14T09:00:00+09:00"}}],
             "confidence": 0.9, "reason": "x"}
    choice = router.choose("다음 주 토요일 아침에 이사 준비하라고 알려 줘", [], NOW, "서울", WrongModel(right))
    assert choice.tools[0]["args"]["due_at"] == "2026-11-14T09:00:00+09:00"
    assert not any("고쳤습니다" in g for g in choice.guards)


def test_memory_date_is_fixed_to_the_date_the_user_meant():
    # 요일은 맞지만(11/7 은 토요일) '다음 주 토요일'(11/14)과 다른 날짜 — 요일 검사로는 못 잡는 경우
    assert memory.fix_memory_date("11월 7일(토) 이사", "나 다음주 토요일에 이사 가", NOW) == "11월 14일(토) 이사"
    assert memory.fix_memory_date("11월 12일 이사", "나 다음 주 토요일에 이사 가", NOW) == "11월 14일 이사"


def test_memory_date_is_left_alone_when_it_matches_or_cannot_be_judged():
    assert memory.fix_memory_date("11월 14일(토) 이사", "나 다음 주 토요일에 이사 가", NOW) == "11월 14일(토) 이사"
    assert memory.fix_memory_date("11월 7일(토) 이사", "이사 갈 예정이야", NOW) == "11월 7일(토) 이사"      # 사용자가 상대 표현을 안 썼다
    assert memory.fix_memory_date("다음 주 토요일 이사", "나 다음 주 토요일에 이사 가", NOW) == "다음 주 토요일 이사"  # 날짜 숫자가 없다


def test_memory_with_two_conflicting_dates_is_not_saved():
    assert memory.fix_memory_date("11월 7일 이사, 11월 9일 정리", "나 다음 주 토요일에 이사 가", NOW) is None


def test_model_picked_list_reminders_is_removed_for_a_statement_but_kept_for_a_question():
    plan = {"tools": [{"name": "list_reminders", "args": {}}], "confidence": 0.9, "reason": "x"}
    statement = router.choose("나 다음주 토요일에 이사 가", [], NOW, "서울", WrongModel(plan))
    assert statement.tools == [] and any("약속 목록 조회" in g for g in statement.guards)
    question = router.choose("내가 다음 주에 뭐 한다고 했지?", [], NOW, "서울", WrongModel(plan))
    assert [t["name"] for t in question.tools] == ["list_reminders"]
