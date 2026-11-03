"""Langfuse 프롬프트 관리(N055 4장)의 약속을 지킨다. 실제 Langfuse 는 부르지 않고 가짜 클라이언트를 쓴다.

- 코드에 남긴 지시문(fallback)은 도구 목록 자리만 변수로 둔 같은 문장이다.
- 라벨은 ENV 가 정하고(production → production, 그 밖 → staging), 평가에서는 버전 번호가 라벨보다 우선한다.
- Langfuse 가 꺼졌거나 오류가 나거나 닿지 못하면 코드의 지시문으로 답하고, 어디서 왔는지(ref)가 남는다.
- 가져온 프롬프트 클라이언트는 도구 선택 결과에 실려 generation 연결에 쓰인다.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import observe, prompts, router

NOW = datetime(2026, 10, 12, 14, 0, tzinfo=ZoneInfo("Asia/Seoul"))


class FakePrompt:
    def __init__(self, version, template, is_fallback=False):
        self.version, self.template, self.is_fallback = version, template, is_fallback

    def compile(self, **variables):
        return prompts.render(self.template, **variables)


class FakeClient:
    def __init__(self, prompt=None, error=None):
        self.prompt, self.error, self.calls = prompt, error, []

    def get_prompt(self, name, **kwargs):
        self.calls.append((name, kwargs))
        if self.error:
            raise self.error
        return self.prompt or FakePrompt(3, kwargs["fallback"], is_fallback=False)


class FakeLLM:
    can_reason = True
    model = "fake-model"

    def __init__(self):
        self.system = None

    def complete_json(self, system, user, usage=None):
        self.system = system
        return {"tools": [], "confidence": 0.9, "reason": "시험"}


@pytest.fixture(autouse=True)
def reset_client():
    prompts._down_until.clear()
    yield
    observe.set_client_for_test(None)
    prompts._down_until.clear()


def test_code_prompt_is_the_template_with_the_tool_list_filled_in():
    assert "{{tools}}" in router.router_template()
    assert router.router_template().count("{{") == 1
    assert prompts.render(router.router_template(), tools=router.tools_block()) == router.router_prompt()
    assert router.tools_block() in router.router_prompt()


def test_label_follows_env(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    assert prompts.default_label() == "production"
    monkeypatch.setenv("ENV", "staging")
    assert prompts.default_label() == "staging"
    monkeypatch.delenv("ENV")
    assert prompts.default_label() == "staging"


def test_without_langfuse_the_code_prompt_is_used():
    observe.set_client_for_test(None)
    got = prompts.fetch_text_prompt("tool-router", "안녕 {{tools}}", {"tools": "도구"})
    assert got.text == "안녕 도구" and got.ref.startswith("code(") and got.client is None


def test_fetch_by_label_fills_the_variables_and_keeps_the_client(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    remote = FakePrompt(7, "원격 {{tools}}")
    client = FakeClient(remote)
    observe.set_client_for_test(client)
    got = prompts.fetch_text_prompt("tool-router", "코드 {{tools}}", {"tools": "도구"})
    assert got.text == "원격 도구" and got.ref == "tool-router v7 (production)" and got.client is remote
    name, kwargs = client.calls[0]
    assert kwargs["label"] == "production" and "version" not in kwargs
    assert "fallback" not in kwargs        # 없는 라벨과 닿지 못함을 구분하려고 SDK 의 fallback 을 쓰지 않고 우리가 처리한다


def test_version_wins_over_label():
    client = FakeClient(FakePrompt(1, "v1 {{tools}}"))
    observe.set_client_for_test(client)
    got = prompts.fetch_text_prompt("tool-router", "코드 {{tools}}", {"tools": "도구"}, label="production", version=1)
    assert got.ref == "tool-router v1 (v1)"
    assert client.calls[0][1]["version"] == 1 and "label" not in client.calls[0][1]


def test_an_error_while_fetching_falls_back_to_the_code_prompt():
    observe.set_client_for_test(FakeClient(error=RuntimeError("연결 안 됨")))
    got = prompts.fetch_text_prompt("tool-router", "코드 {{tools}}", {"tools": "도구"})
    assert got.text == "코드 도구" and got.ref.startswith("code(가져오기 실패") and got.client is None


def test_sdk_fallback_object_is_reported_as_code_and_not_linked():
    observe.set_client_for_test(FakeClient(FakePrompt(0, "코드 {{tools}}", is_fallback=True)))
    got = prompts.fetch_text_prompt("tool-router", "코드 {{tools}}", {"tools": "도구"})
    assert got.text == "코드 도구" and "닿지 못함" in got.ref and got.client is None


def test_after_a_failure_it_stops_asking_for_a_while_and_answers_at_once_from_code():
    client = FakeClient(error=RuntimeError("연결 안 됨"))
    observe.set_client_for_test(client)
    first = prompts.fetch_text_prompt("tool-router", "코드 {{tools}}", {"tools": "도구"})
    second = prompts.fetch_text_prompt("tool-router", "코드 {{tools}}", {"tools": "도구"})
    assert len(client.calls) == 1                      # 두 번째는 묻지 않았다
    assert second.text == first.text == "코드 도구" and "쉬는 중" in second.ref
    prompts._down_until.clear()                          # 쉬는 시간이 지나면 다시 묻는다
    prompts.fetch_text_prompt("tool-router", "코드 {{tools}}", {"tools": "도구"})
    assert len(client.calls) == 2


def test_model_choose_uses_the_fetched_prompt_and_reports_where_it_came_from():
    remote = FakePrompt(2, "원격 지시문\n{{tools}}")
    observe.set_client_for_test(FakeClient(remote))
    llm = FakeLLM()
    choice = router.model_choose("안녕", [], NOW, "서울", llm, label="staging")
    assert llm.system.startswith("원격 지시문") and router.tools_block() in llm.system
    assert choice.prompt == "tool-router v2 (staging)" and choice.prompt_client is remote


def test_model_choose_without_langfuse_sends_exactly_the_code_prompt():
    observe.set_client_for_test(None)
    llm = FakeLLM()
    choice = router.model_choose("안녕", [], NOW, "서울", llm)
    assert llm.system == router.router_prompt()
    assert choice.prompt.startswith("code(") and choice.prompt_client is None


def test_a_missing_label_does_not_start_the_cooldown_so_another_label_can_still_be_tried():
    class NotFoundError(Exception):
        pass

    observe.set_client_for_test(FakeClient(error=NotFoundError("없음")))
    first = prompts.fetch_text_prompt("tool-router", "코드 {{tools}}", {"tools": "도구"}, label="prod-b")
    assert first.text == "코드 도구" and "없음" in first.ref and "쉬는 중" not in first.ref
    assert prompts._down_until == {}                    # 닿지 못한 것이 아니므로 쉬지 않는다
