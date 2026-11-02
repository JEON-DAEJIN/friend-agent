"""좋아요·별로예요·신고가 그 답의 트레이스에 Langfuse 점수로 붙는지(N054 8장). 실제 Langfuse 는 부르지 않는다."""
import json

import pytest
from fastapi.testclient import TestClient

from app import observe
from app.main import app, store

client = TestClient(app)


class Obs:
    trace_id = "trace-abc"

    def start_observation(self, **kwargs):
        return self

    def update(self, **kwargs):
        return self

    def end(self, **kwargs):
        return None


class Recorder:
    def __init__(self):
        self.scores = []

    def start_observation(self, **kwargs):
        return Obs()

    def create_score(self, **kwargs):
        self.scores.append(kwargs)

    def flush(self):
        pass


@pytest.fixture
def recorder():
    fake = Recorder()
    observe.set_client_for_test(fake)
    yield fake
    observe.set_client_for_test(None)


def answer_id(message="오늘 좀 지쳤어"):
    r = client.post("/api/chat", json={"message": message})
    events = [json.loads(line) for line in r.text.splitlines()]
    return next(e for e in events if e["type"] == "done")["message_id"]


def test_answer_message_keeps_the_trace_id_so_a_reaction_can_find_it(recorder):
    message_id = answer_id()
    assert store.get_message("local-user", message_id)["meta"]["trace_id"] == "trace-abc"


def test_like_and_dislike_become_a_boolean_user_feedback_score_on_that_trace(recorder):
    message_id = answer_id()
    assert client.post("/api/feedback", json={"message_id": message_id, "value": "like"}).json() == {"ok": True, "scored": True}
    assert client.post("/api/feedback", json={"message_id": message_id, "value": "dislike"}).json()["scored"] is True
    assert recorder.scores == [
        {"name": "user-feedback", "value": 1.0, "trace_id": "trace-abc", "data_type": "BOOLEAN"},
        {"name": "user-feedback", "value": 0.0, "trace_id": "trace-abc", "data_type": "BOOLEAN"},
    ]


def test_report_becomes_a_report_score_with_the_reason_as_comment_and_personal_data_masked(recorder):
    message_id = answer_id()
    client.post("/api/report", json={"message_id": message_id, "reason": "부적절해요 010-1234-5678"})
    score = recorder.scores[0]
    assert (score["name"], score["value"], score["trace_id"], score["data_type"]) == ("report", 1.0, "trace-abc", "BOOLEAN")
    assert "010-1234" not in score["comment"] and "[전화번호]" in score["comment"]


def test_nobody_can_score_someone_elses_message(recorder):
    other = store.add_message("someone-else", "assistant", "남의 답", {"trace_id": "trace-other"})
    assert client.post("/api/feedback", json={"message_id": other["id"], "value": "dislike"}).status_code == 404
    assert client.post("/api/report", json={"message_id": other["id"], "reason": ""}).status_code == 404
    assert recorder.scores == []


def test_only_answers_can_be_scored_not_my_own_messages(recorder):
    mine = store.add_message("local-user", "user", "내가 한 말")
    assert client.post("/api/feedback", json={"message_id": mine["id"], "value": "like"}).status_code == 404


def test_without_langfuse_keys_the_reaction_is_accepted_but_no_score_is_sent():
    observe.set_client_for_test(None)
    message_id = answer_id()
    assert client.post("/api/feedback", json={"message_id": message_id, "value": "dislike"}).json() == {"ok": True, "scored": False}


def test_unknown_value_is_rejected(recorder):
    message_id = answer_id()
    assert client.post("/api/feedback", json={"message_id": message_id, "value": "meh"}).status_code == 400
    assert recorder.scores == []
