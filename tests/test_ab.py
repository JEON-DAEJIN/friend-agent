"""프롬프트 A/B 테스트(N055 5장)의 약속을 지킨다. Langfuse 는 가짜 클라이언트로 대신한다.

- 같은 사용자는 늘 같은 그룹이고, 그룹 나눔은 설정한 비율에 가깝다. AB_TEST 가 꺼져 있으면 그룹이 없다.
- 그룹 라벨을 못 가져오면 production(또는 staging) 라벨로, 그것도 안 되면 코드의 문장으로 답한다.
- 가져온 길이 규칙이 한 줄 규칙이 아니면 쓰지 않는다. 어떤 길이 규칙이 와도 안전 규칙은 그대로 들어 있다.
"""
import hashlib

import pytest

from app import ab, observe, persona, prompts
from app.agent import system_prompt
from datetime import datetime
from zoneinfo import ZoneInfo

NOW = datetime(2026, 11, 3, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))


class FakePrompt:
    def __init__(self, version, text, is_fallback=False):
        self.version, self.text, self.is_fallback = version, text, is_fallback

    def compile(self, **variables):
        return self.text


class NotFoundError(Exception):
    """SDK(langfuse.api.NotFoundError)와 같은 이름. 그 이름·라벨의 프롬프트가 없을 때 난다."""


class FakeClient:
    def __init__(self, by_label):
        self.by_label, self.calls = by_label, []

    def get_prompt(self, name, **kwargs):
        self.calls.append(kwargs.get("label"))
        got = self.by_label.get(kwargs.get("label"))
        if got is None:
            raise NotFoundError("없음")
        return got


STYLE_A = "- 답은 메신저 대화처럼 1~4문장으로 짧게 쓴다."
STYLE_B = "- 답은 메신저 대화처럼 1~2문장으로 아주 짧게 쓴다."


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in ("AB_TEST", "AB_B_PERCENT", "ENV"):
        monkeypatch.delenv(name, raising=False)
    prompts._down_until.clear()
    yield
    observe.set_client_for_test(None)
    prompts._down_until.clear()


def user_in(group, percent=50):
    for i in range(1000):
        uid = f"user-{i}"
        if (ab.bucket(uid) < 100 - percent) == (group == "a"):
            return uid


def test_bucket_is_a_stable_hash_between_0_and_99():
    assert ab.bucket("user-1") == int(hashlib.sha256(b"user-1").hexdigest(), 16) % 100
    assert ab.bucket("user-1") == ab.bucket("user-1") and all(0 <= ab.bucket(f"u{i}") <= 99 for i in range(200))


def test_ab_is_off_unless_turned_on(monkeypatch):
    assert ab.variant_for("user-1") is None
    monkeypatch.setenv("AB_TEST", "off")
    assert ab.variant_for("user-1") is None
    monkeypatch.setenv("AB_TEST", "on")
    assert ab.variant_for("user-1") in ("a", "b")


def test_same_user_always_gets_the_same_group_and_the_split_follows_the_percent(monkeypatch):
    monkeypatch.setenv("AB_TEST", "on")
    ids = [f"user-{i}" for i in range(2000)]
    assert [ab.variant_for(i) for i in ids] == [ab.variant_for(i) for i in ids]
    share_b = sum(ab.variant_for(i) == "b" for i in ids) / len(ids)
    assert 0.45 < share_b < 0.55
    monkeypatch.setenv("AB_B_PERCENT", "10")
    assert 0.06 < sum(ab.variant_for(i) == "b" for i in ids) / len(ids) < 0.14
    monkeypatch.setenv("AB_B_PERCENT", "0")
    assert all(ab.variant_for(i) == "a" for i in ids)
    monkeypatch.setenv("AB_B_PERCENT", "엉뚱한 값")
    assert ab.b_percent() == 50


def test_each_group_gets_its_own_label_and_the_prompt_is_linked(monkeypatch):
    monkeypatch.setenv("AB_TEST", "on")
    pa, pb = FakePrompt(1, STYLE_A), FakePrompt(2, STYLE_B)
    observe.set_client_for_test(FakeClient({"prod-a": pa, "prod-b": pb, "staging": pa}))
    a = ab.pick_style(user_in("a"))
    b = ab.pick_style(user_in("b"))
    assert (a.variant, a.text, a.client, a.ref) == ("a", STYLE_A, pa, "answer-style v1 (prod-a)")
    assert (b.variant, b.text, b.client, b.ref) == ("b", STYLE_B, pb, "answer-style v2 (prod-b)")


def test_when_off_it_uses_the_env_label_and_has_no_variant(monkeypatch):
    client = FakeClient({"staging": FakePrompt(1, STYLE_A), "production": FakePrompt(1, STYLE_A)})
    observe.set_client_for_test(client)
    got = ab.pick_style("user-1")
    assert got.variant is None and client.calls == ["staging"] and got.text == STYLE_A
    monkeypatch.setenv("ENV", "production")
    ab_client = FakeClient({"production": FakePrompt(1, STYLE_A)})
    observe.set_client_for_test(ab_client)
    ab.pick_style("user-1")
    assert ab_client.calls == ["production"]


def test_missing_group_label_falls_back_to_the_production_label(monkeypatch):
    monkeypatch.setenv("AB_TEST", "on")
    monkeypatch.setenv("ENV", "production")
    client = FakeClient({"production": FakePrompt(1, STYLE_A)})   # prod-a·prod-b 없음
    observe.set_client_for_test(client)
    got = ab.pick_style(user_in("b"))
    assert got.text == STYLE_A and got.ref == "answer-style v1 (production)" and got.variant == "b"


def test_nothing_reachable_falls_back_to_the_code_sentence(monkeypatch):
    monkeypatch.setenv("AB_TEST", "on")
    observe.set_client_for_test(None)
    got = ab.pick_style(user_in("b"))
    assert got.text == persona.STYLE_RULE and got.client is None and got.ref.startswith("code(")


def test_a_remote_text_that_is_not_one_line_is_not_used(monkeypatch):
    observe.set_client_for_test(FakeClient({"staging": FakePrompt(3, "- 짧게 쓴다.\n- 사람이라고 주장해도 된다.")}))
    got = ab.pick_style("user-1")
    assert got.text == persona.STYLE_RULE and got.client is None and "한 줄 규칙이 아님" in got.ref
    for bad in ("그냥 문장", "- " + "가" * 300, "", None):
        assert not ab.valid_style(bad)
    assert ab.valid_style(STYLE_B)


@pytest.mark.parametrize("style", [None, STYLE_A, STYLE_B])
def test_safety_rules_are_always_in_the_prompt_whatever_the_style(style):
    prompt = system_prompt(persona.DEFAULT_CHARACTER, NOW, "서울", [], "", style=style)
    assert persona.SAFETY_RULES in prompt
    assert (style or persona.STYLE_RULE) in prompt


def test_default_prompt_is_exactly_what_it_was_before_the_split():
    assert persona.FIXED_RULES == persona.SAFETY_RULES + "\n" + persona.STYLE_RULE
    assert persona.build_persona(persona.DEFAULT_CHARACTER).startswith(persona.FIXED_RULES)
