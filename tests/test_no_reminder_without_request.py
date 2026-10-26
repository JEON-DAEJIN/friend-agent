# tests/test_no_reminder_without_request.py
import json

from fastapi.testclient import TestClient

from app.main import app, store

client = TestClient(app)


def chat(message):
    r = client.post("/api/chat", json={"message": message})
    assert r.status_code == 200
    return [json.loads(line) for line in r.text.splitlines()]


def test_schedule_mention_without_request_is_not_saved():
    # 예시 E08: "이번 주 금요일에 자격증 시험 있어" → 알려 달라는 부탁이 없으니 약속을 저장하지 않는다
    before = len(store.list_reminders("local-user", status="pending"))
    turn = next(e for e in chat("이번 주 금요일에 자격증 시험 있어") if e["type"] == "turn")
    assert "create_reminder" not in [t["name"] for t in turn["tools"]]
    assert len(store.list_reminders("local-user", status="pending")) == before
