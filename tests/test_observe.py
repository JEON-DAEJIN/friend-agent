"""관측(app/observe.py) 계측이 약속한 모양으로 남는지 확인한다. 실제 Langfuse 나 AI 는 부르지 않는다.

가짜 클라이언트가 관찰 단위를 트리로 받아 적고, 이름·종류·남긴 값·닫힘을 검사한다.
"""
from types import SimpleNamespace

import pytest

from app import agent, observe
from app.llm import ClaudeLLM, FakeLLM, OpenAICompatibleLLM
from app.store.memory_store import MemoryStore


class Obs:
    def __init__(self, name, as_type="span", **fields):
        self.name, self.as_type, self.fields = name, as_type, dict(fields)
        self.children, self.ended = [], False
        self.trace_id = "trace-1"

    def start_observation(self, *, name, as_type="span", **fields):
        child = Obs(name, as_type, **fields)
        self.children.append(child)
        return child

    def update(self, **fields):
        self.fields.update(fields)
        return self

    def end(self, **kwargs):
        self.ended = True


class Client:
    def __init__(self):
        self.roots = []
        self.flushes = 0

    def flush(self):
        self.flushes += 1

    def start_observation(self, *, name, as_type="span", **fields):
        root = Obs(name, as_type, **fields)
        self.roots.append(root)
        return root


@pytest.fixture
def client():
    fake = Client()
    observe.set_client_for_test(fake)
    yield fake
    observe.set_client_for_test(None)


def run(message, llm=None):
    store = MemoryStore()
    events = list(agent.run_turn(message, "u1", llm or FakeLLM(delay=0), store, False))
    return events


def names(obs):
    return [c.name for c in obs.children]


def test_mask_hides_email_card_and_phone_inside_nested_data():
    data = {"a": "연락은 kim.test@example.com 으로", "b": ["카드 1234-5678-9012-3456", "전화 010-1234-5678"], "n": 3}
    masked = observe.mask(data=data)
    assert "example.com" not in str(masked) and "1234-5678-9012" not in str(masked) and "010-1234" not in str(masked)
    assert masked["n"] == 3


def test_plain_turn_leaves_chat_turn_with_safety_choose_reply_memory(client):
    run("오늘 좀 지쳤어")
    root = client.roots[0]
    assert (root.name, root.as_type) == ("chat-turn", "span")
    assert names(root) == ["load-context", "safety", "choose-tools", "reply", "save-turn", "memory-update", "summary-check"]
    kinds = {c.name: c.as_type for c in root.children}
    assert kinds == {"load-context": "span", "safety": "span", "choose-tools": "generation", "reply": "generation",
                     "save-turn": "span", "memory-update": "generation", "summary-check": "span"}
    assert all(c.ended for c in root.children) and root.ended


def test_rule_choice_is_marked_decided_by_rule_with_zero_tokens(client):
    run("오늘 좀 지쳤어")
    choose = client.roots[0].children[2]
    assert choose.fields["metadata"]["decided_by"] == "rule"
    assert choose.fields["model"] == "fake"
    assert choose.fields["usage_details"] == {"input": 0, "output": 0}


def test_reply_records_first_token_time_and_full_answer(client):
    events = run("오늘 좀 지쳤어")
    reply = client.roots[0].children[3]
    answer = "".join(e["text"] for e in events if e["type"] == "delta")
    assert reply.fields["output"] == answer
    assert reply.fields["completion_start_time"] is not None
    assert reply.fields["usage_details"] == {"input": 0, "output": 0}


def test_tool_gets_its_own_span_with_ok_flag(client, monkeypatch):
    stub = SimpleNamespace(name="get_weather", ok=True, summary="서울 맑음", data={})
    monkeypatch.setattr(agent, "run_tool", lambda name, args, ctx: stub)
    run("내일 서울 날씨 어때?")
    root = client.roots[0]
    assert names(root) == ["load-context", "safety", "choose-tools", "tool:get_weather", "reply", "save-turn",
                           "memory-update", "summary-check"]
    tool = root.children[3]
    assert tool.as_type == "span" and tool.fields["metadata"] == {"ok": True} and tool.fields["output"] == "서울 맑음"


def test_crisis_message_stops_after_safety_span(client):
    run("요즘 너무 죽고 싶어")
    root = client.roots[0]
    assert names(root) == ["load-context", "safety"]
    assert root.children[1].fields["output"] == {"crisis": True}


def test_greeting_makes_its_own_trace_only_when_it_speaks(client):
    store = MemoryStore()
    assert agent.greet("u1", FakeLLM(delay=0), store)["text"]  # 대화가 처음이라 먼저 말을 건다
    assert [r.name for r in client.roots] == ["greeting"]
    assert names(client.roots[0]) == ["greeting-reply"]
    assert agent.greet("u1", FakeLLM(delay=0), store) is None  # 방금 말했으니 이번엔 조용
    assert len(client.roots) == 1


def test_tracing_off_leaves_the_app_working():
    observe.set_client_for_test(None)
    events = run("오늘 좀 지쳤어")
    assert events[-1]["type"] == "done"


def test_a_broken_client_never_breaks_a_turn():
    class Broken:
        def start_observation(self, **kwargs):
            raise RuntimeError("관측 서버 고장")

    observe.set_client_for_test(Broken())
    try:
        assert run("오늘 좀 지쳤어")[-1]["type"] == "done"
    finally:
        observe.set_client_for_test(None)


# ── 세 LLM 클래스가 같은 방식으로 토큰을 남긴다 ─────────────────────

def choice(content):
    return SimpleNamespace(message=SimpleNamespace(content=content), delta=SimpleNamespace(content=content))


class OpenAICompletions:
    def create(self, **kwargs):
        if kwargs.get("stream"):
            self.stream_options = kwargs.get("stream_options")
            return iter([SimpleNamespace(choices=[choice("안")], usage=None),
                         SimpleNamespace(choices=[choice("녕")], usage=None),
                         SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=11, completion_tokens=2))])
        return SimpleNamespace(choices=[choice('{"tools": []}')], usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3))


def make_openai():
    llm = object.__new__(OpenAICompatibleLLM)
    llm.model = "gpt-4o-mini"
    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=OpenAICompletions()))
    return llm


def test_openai_records_model_and_tokens_for_json_and_stream(client):
    llm = make_openai()
    traced = observe.TracedLLM(llm, observe.start_trace("chat-turn"))
    with traced.step("choose-tools"):
        traced.complete_json("시스템", "사용자")
    with traced.step("reply"):
        assert "".join(traced.chat_stream([{"role": "user", "content": "hi"}])) == "안녕"
    choose, reply = client.roots[0].children
    assert (choose.fields["model"], choose.fields["usage_details"]) == ("gpt-4o-mini", {"input": 7, "output": 3})
    assert reply.fields["usage_details"] == {"input": 11, "output": 2}
    assert llm.client.chat.completions.stream_options == {"include_usage": True}


class ClaudeMessages:
    def create(self, **kwargs):
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text='{"tools": []}')],
                               usage=SimpleNamespace(input_tokens=21, output_tokens=5))

    def stream(self, **kwargs):
        class Stream:
            text_stream = iter(["안", "녕"])

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get_final_message(self):
                return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=30, output_tokens=4))

        return Stream()


def test_claude_records_model_and_tokens_for_json_and_stream(client):
    llm = ClaudeLLM(api_key="test-key", model="claude-opus-5-5")
    llm.client = SimpleNamespace(beta=SimpleNamespace(messages=ClaudeMessages()))
    traced = observe.TracedLLM(llm, observe.start_trace("chat-turn"))
    with traced.step("choose-tools"):
        traced.complete_json("시스템", "사용자")
    with traced.step("reply"):
        assert "".join(traced.chat_stream([{"role": "user", "content": "hi"}])) == "안녕"
    choose, reply = client.roots[0].children
    assert (choose.fields["model"], choose.fields["usage_details"]) == ("claude-opus-5-5", {"input": 21, "output": 5})
    assert reply.fields["usage_details"] == {"input": 30, "output": 4}


# ── 7장: 트레이스 속성(user_id, session_id, environment, version, tags, metadata) ──

def test_trace_attrs_use_user_id_korean_date_environment_and_revision(monkeypatch):
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setenv("K_REVISION", "friend-agent-staging-00007")
    monkeypatch.setenv("COMMIT_SHA", "abc1234")
    attrs = observe.trace_attrs("user-1")
    assert attrs["user_id"] == "user-1"
    assert attrs["session_id"].startswith("user-1:") and len(attrs["session_id"].split(":")[1]) == 10
    assert (attrs["environment"], attrs["version"]) == ("staging", "friend-agent-staging-00007")
    assert attrs["metadata"] == {"commit": "abc1234"}
    assert "@" not in attrs["user_id"]


def test_local_run_without_cloud_env_is_marked_local(monkeypatch):
    for name in ("ENV", "K_REVISION", "COMMIT_SHA"):
        monkeypatch.delenv(name, raising=False)
    attrs = observe.trace_attrs("user-1")
    assert (attrs["environment"], attrs["version"], attrs["metadata"]["commit"]) == ("local", "local", "local")


def test_every_child_gets_the_attributes_including_tags_added_later(client, monkeypatch):
    import contextlib
    import langfuse

    seen = []

    @contextlib.contextmanager
    def fake_propagate(**attrs):
        seen.append({k: (list(v) if isinstance(v, list) else v) for k, v in attrs.items()})
        yield

    monkeypatch.setattr(langfuse, "propagate_attributes", fake_propagate)
    root = observe.start_trace("chat-turn", input="hi", user_id="user-1")
    root.start_observation(name="safety")
    root.tag("get_weather")
    root.start_observation(name="reply")
    assert [s["user_id"] for s in seen] == ["user-1"] * 3  # 루트와 자식 둘 모두
    assert "tags" not in seen[0] and "tags" not in seen[1]
    assert seen[2]["tags"] == ["get_weather"]  # 태그는 알게 된 뒤에 만드는 것부터 붙는다
    assert seen[2]["session_id"] == seen[0]["session_id"]


def test_buffer_is_flushed_once_when_a_turn_or_greeting_ends(client):
    run("오늘 좀 지쳤어")
    assert client.flushes == 1
    agent.greet("u2", FakeLLM(delay=0), MemoryStore())
    assert client.flushes == 2


def test_flush_failure_does_not_break_the_turn(client):
    def broken():
        raise RuntimeError("전송 실패")

    client.flush = broken
    assert run("오늘 좀 지쳤어")[-1]["type"] == "done"


def test_buffer_is_flushed_before_the_last_event_is_sent(client):
    """Cloud Run 은 마지막 이벤트(done)를 보낸 뒤 CPU 를 거의 안 주므로 flush 는 done 앞에 있어야 한다."""
    flushes_when_done = None
    for event in agent.run_turn("오늘 좀 지쳤어", "u1", FakeLLM(delay=0), MemoryStore(), False):
        if event["type"] == "done":
            flushes_when_done = client.flushes
    assert flushes_when_done == 1
    assert client.flushes == 1  # 끝난 뒤 finally 가 다시 보내지 않는다


def test_crisis_turn_also_flushes_before_done(client):
    flushes_when_done = None
    for event in agent.run_turn("요즘 너무 죽고 싶어", "u1", FakeLLM(delay=0), MemoryStore(), False):
        if event["type"] == "done":
            flushes_when_done = client.flushes
    assert flushes_when_done == 1
