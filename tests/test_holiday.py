"""'다음 공휴일' 질문에서 지어낸 답이 나온 일(N054 6장 실습)의 재발 방지.

원인은 두 곳이었다. ① 도구 선택이 '다음 공휴일'에 한 달 범위를 골라 12월 25일을 못 찾았다.
② 도구가 '없다'고 했는데 답 만들기가 있는 것처럼 지어냈다. 실제 AI 의 답은 매번 달라 테스트로 판정할 수 없으므로,
여기서는 고친 지시문과 도구의 동작이 그대로 있는지만 확인한다. 실제 답의 변화는 Langfuse 트레이스로 확인했다.
"""
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app import persona, router, tools


def test_router_prompt_tells_model_to_pick_get_holidays_with_empty_args_for_next_holiday():
    prompt = router.router_prompt()
    assert '{"name": "get_holidays", "args": {}}' in prompt
    assert "tools 를 빈 배열로 두면 안 된다" in prompt


def test_keyword_rule_also_leaves_args_empty_for_next_holiday():
    now = datetime(2026, 11, 2, 9, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    choice = router.rule_choose("다음 공휴일이 언제야?", now, "서울")
    assert choice.tools == [{"name": "get_holidays", "args": {}}]


def test_empty_args_look_a_year_ahead_and_find_christmas(monkeypatch):
    monkeypatch.setattr(tools, "holidays_of", lambda year: [(date(year, 12, 25), "크리스마스")])
    result = tools.get_holidays({}, SimpleNamespace(timezone="Asia/Seoul"))
    assert result.ok and "크리스마스" in result.summary


def test_no_holiday_result_says_the_range_has_none_and_gives_the_next_one_outside_it(monkeypatch):
    monkeypatch.setattr(tools, "holidays_of", lambda year: [(date(year, 12, 25), "크리스마스")])
    result = tools.get_holidays({"from": "2026-11-02", "to": "2026-11-30"}, SimpleNamespace(timezone="Asia/Seoul"))
    assert "공휴일이 없습니다" in result.summary and "조회 범위 밖에서 찾아 덧붙임" in result.summary
    assert "12월 25일(금) 크리스마스" in result.summary      # N055: 지어낼 빈칸을 코드가 채운다


def test_no_holiday_result_keeps_the_old_sentence_when_the_next_one_cannot_be_found(monkeypatch):
    calls = []

    def holidays_of(year):
        calls.append(year)
        if len(calls) > 1:        # 기간 안의 해는 읽고, 그 뒤 공휴일을 찾는 조회는 실패
            raise RuntimeError("연결 안 됨")
        return []

    monkeypatch.setattr(tools, "holidays_of", holidays_of)
    result = tools.get_holidays({"from": "2026-11-02", "to": "2026-11-30"}, SimpleNamespace(timezone="Asia/Seoul"))
    assert "공휴일이 없습니다" in result.summary and "이 기간 밖은 조회하지 않았습니다" in result.summary


def test_persona_rules_forbid_inventing_when_the_tool_said_none():
    assert "꾸며 말하지 않는다" in persona.FIXED_RULES
    assert "꾸며 말하지 않는다" in persona.build_persona(persona.DEFAULT_CHARACTER)


# ── 앞선 대화에 틀린 답이 쌓여 있어도 정확한 도구 결과를 따르게 하는 위치 ─────────────

class RecordingLLM:
    """chat_stream 에 보낸 메시지를 적어 두는 가짜 LLM."""
    can_reason = False
    model = "fake"

    def __init__(self):
        self.sent = None

    def chat_stream(self, messages, usage=None):
        self.sent = messages
        yield "ok"


def run_with(monkeypatch, message, tool_result):
    from app import agent
    from app.store.memory_store import MemoryStore

    if tool_result:
        monkeypatch.setattr(agent, "run_tool", lambda name, args, ctx: tool_result)
    llm = RecordingLLM()
    list(agent.run_turn(message, "u1", llm, MemoryStore(), False))
    return llm.sent


def test_tool_result_is_repeated_right_before_the_question(monkeypatch):
    stub = SimpleNamespace(name="get_holidays", ok=True, summary="공휴일: 12월 25일(금) 크리스마스", data={})
    sent = run_with(monkeypatch, "다음 공휴일이 언제야?", stub)
    assert sent[-1] == {"role": "user", "content": "다음 공휴일이 언제야?"}
    assert sent[-2]["role"] == "system"
    assert "방금 조회한 도구 결과" in sent[-2]["content"] and "12월 25일" in sent[-2]["content"]


def test_no_tool_note_when_no_tool_was_used(monkeypatch):
    sent = run_with(monkeypatch, "오늘 좀 지쳤어", None)
    assert sent[-1]["role"] == "user"
    assert all("방금 조회한 도구 결과" not in m["content"] for m in sent)
