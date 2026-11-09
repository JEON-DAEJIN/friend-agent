# tests/test_cancel_target.py — N059: 취소 요청이 다른 약속을 지우지 않는다
# 로그 근거: "치과 약속은 취소할래"에 저장된 약속이 '팀 회의' 하나뿐이었는데 그것을 취소하고 "치과 약속을 취소했어"라고 답했다(트레이스 7a5ba7e4).
from datetime import datetime, timedelta, timezone

from app.main import store
from app.tools import Context, cancel_reminder


def setup(user, *texts):
    due = datetime.now(timezone.utc) + timedelta(days=3)
    for text in texts:
        store.add_reminder(user, text, due)
    return Context(store=store, user_id=user, location="서울", timezone="Asia/Seoul")


def pending(user):
    return [r["text"] for r in store.list_reminders(user, status="pending")]


def test_name_that_matches_nothing_does_not_cancel_the_only_reminder():
    ctx = setup("cancel-u1", "팀 회의")
    result = cancel_reminder({"query": "치과"}, ctx)
    assert result.ok is False and "찾지 못" in result.summary
    assert pending("cancel-u1") == ["팀 회의"]


def test_name_that_matches_cancels_that_reminder():
    ctx = setup("cancel-u2", "팀 회의", "치과")
    result = cancel_reminder({"query": "치과"}, ctx)
    assert result.ok is True
    assert pending("cancel-u2") == ["팀 회의"]


def test_unmatched_name_with_two_reminders_cancels_nothing():
    ctx = setup("cancel-u3", "팀 회의", "물 마시기")
    assert cancel_reminder({"query": "치과"}, ctx).ok is False
    assert len(pending("cancel-u3")) == 2


def test_generic_words_still_cancel_the_only_reminder():
    for number, query in enumerate(["약속", "그 약속", "아까 한 약속", "알림", ""]):
        user = f"cancel-u4-{number}"
        ctx = setup(user, "팀 회의")
        assert cancel_reminder({"query": query}, ctx).ok is True, query
        assert pending(user) == []
