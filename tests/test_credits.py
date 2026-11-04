"""크레딧 원장(N056 4장). 가짜 LLM과 메모리 저장소로, 테스트마다 다른 사용자로 확인한다.

다른 테스트가 쓰는 local-user 의 잔액을 건드리지 않으려고 로그인 사용자를 테스트 전용 사용자로 바꿔 끼운다.
"""
import itertools
import json
import threading

import pytest
from fastapi.testclient import TestClient

from app import main
from app.auth import User
from app.main import app, current_user, store

client = TestClient(app)
_ids = itertools.count(1)


@pytest.fixture
def uid():
    user_id = f"credit-test-{next(_ids)}"
    app.dependency_overrides[current_user] = lambda: User(user_id, anonymous=True)
    yield user_id
    app.dependency_overrides.pop(current_user, None)


def send(message):
    return client.post("/api/chat", json={"message": message})


def drain(user_id):
    """잔액을 0으로 만든다(교안 4장 확인 5번: 원장에 −줄을 직접 하나 넣기)."""
    client.get("/api/credits")
    store.add_credit(user_id, -store.credit_balance(user_id), "adjust", f"adjust:test:{user_id}")


def test_new_user_gets_free_credits_once(uid):
    assert client.get("/api/me").json()["credits"] == main.settings.free_credits
    client.get("/api/me")
    body = client.get("/api/credits").json()
    assert body["balance"] == main.settings.free_credits
    assert [e["reason"] for e in body["history"]].count("signup") == 1


def test_message_spends_one_credit_and_leaves_a_ledger_row(uid):
    before = client.get("/api/credits").json()["balance"]
    r = send("안녕")
    assert r.status_code == 200
    body = client.get("/api/credits").json()
    assert body["balance"] == before - 1
    assert body["history"][0]["delta"] == -1 and body["history"][0]["reason"] == "chat"


def test_chat_without_credits_is_rejected_with_402(uid):
    drain(uid)
    saved = len(store.recent_messages(uid, 50))
    r = send("안녕")
    assert r.status_code == 402
    assert r.json() == {"error": "insufficient_credits", "message": main.NO_CREDITS_MESSAGE, "balance": 0}
    assert len(store.recent_messages(uid, 50)) == saved   # 답을 만들지 않았다


def test_first_chat_without_calling_me_still_gets_free_credits(uid):
    """화면을 거치지 않고 /api/chat 을 바로 불러도 가입 무료 크레딧이 먼저 쌓이고 나서 빠진다."""
    assert send("안녕").status_code == 200
    assert store.credit_balance(uid) == main.settings.free_credits - 1


def test_crisis_message_is_answered_even_with_zero_credits(uid):
    drain(uid)
    r = send("요즘 너무 죽고 싶어")
    assert r.status_code == 200
    text = "".join(e.get("text", "") for e in map(json.loads, r.text.splitlines()) if e["type"] == "delta")
    assert "109" in text
    assert store.credit_balance(uid) == 0   # 받지도 거절하지도 않는다


def test_failed_answer_refunds_the_credit(uid, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("LLM 장애 흉내")
        yield  # noqa: unreachable — 제너레이터로 만들기 위해

    before = client.get("/api/credits").json()["balance"]
    monkeypatch.setattr(main.agent, "run_turn", broken)
    r = send("안녕")
    assert r.status_code == 200
    assert json.loads(r.text.splitlines()[-1])["type"] == "error"
    history = client.get("/api/credits").json()["history"]
    assert store.credit_balance(uid) == before
    assert [e["reason"] for e in history[:2]] == ["refund", "chat"]   # 지우지 않고 되돌리는 줄을 쌓았다


def test_delete_my_data_keeps_the_ledger(uid):
    send("안녕")
    left = store.credit_balance(uid)
    assert client.delete("/api/data").json() == {"ok": True}
    assert client.get("/api/me").json()["credits"] == left   # 무료 크레딧이 다시 지급되지 않는다


def test_same_ref_is_recorded_only_once(uid):
    assert store.add_credit(uid, 100, "purchase", "order:test-1") is True
    assert store.add_credit(uid, 100, "purchase", "order:test-1") is False


def test_concurrent_spends_cannot_go_below_zero(uid):
    """잔액 1에서 요청 10개가 동시에 와도 한 개만 통과한다(잔액 확인과 기록을 잠금 하나로 묶음)."""
    drain(uid)
    store.add_credit(uid, 1, "adjust", f"adjust:one:{uid}")
    results = []
    threads = [threading.Thread(target=lambda i=i: results.append(store.spend_credit(uid, 1, "chat", f"chat:race-{uid}-{i}", 0)))
               for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(ok for ok, _ in results) == 1
    assert store.credit_balance(uid) == 0
