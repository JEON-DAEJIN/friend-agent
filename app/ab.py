"""프롬프트 A/B 테스트 (N055 5장). 답의 길이 규칙 한 줄(answer-style)만 사용자 그룹에 따라 다른 버전으로 쓴다.

- AB_TEST=on 일 때만 동작한다. off 이거나 없으면 ENV 에 맞는 라벨(production / staging)의 버전을 쓴다.
- 그룹은 사용자 ID 를 해시해 0~99 로 바꾼 값으로 정한다. 같은 사용자는 늘 같은 그룹이다(요청마다 무작위로 고르면 말투가 오락가락한다).
  B 에 보내는 비율은 AB_B_PERCENT(기본 50). 기준은 100 - AB_B_PERCENT 로, 그보다 작으면 A(prod-a), 아니면 B(prod-b).
  파이썬 기본 hash() 는 프로그램을 다시 띄울 때마다 값이 달라서 쓰지 않고 hashlib 을 쓴다.
- 그룹 라벨을 못 가져오면 production(또는 staging) 라벨 버전으로, 그것도 안 되면 코드의 문장으로 답한다.
- 안전 규칙은 이 모듈이 건드리지 않는다. 가져온 문장이 '한 줄짜리 규칙'이 아니면 쓰지 않고 코드의 문장을 쓴다.
"""
import hashlib
import os
from dataclasses import dataclass

from app import prompts
from app.logs import log
from app.persona import STYLE_RULE

STYLE_PROMPT_NAME = "answer-style"
MAX_STYLE_CHARS = 200
GROUP_LABELS = {"a": "prod-a", "b": "prod-b"}


def enabled():
    return os.environ.get("AB_TEST", "").strip().lower() in ("on", "1", "true", "yes")


def b_percent():
    try:
        return max(0, min(100, int(os.environ.get("AB_B_PERCENT", "").strip() or 50)))
    except ValueError:
        return 50


def bucket(user_id):
    """사용자 ID → 0~99. 같은 ID 는 언제 어디서 계산해도 같은 값이다."""
    return int(hashlib.sha256(str(user_id).encode("utf-8")).hexdigest(), 16) % 100


def variant_for(user_id):
    """'a' 또는 'b'. A/B 가 꺼져 있으면 None."""
    if not enabled():
        return None
    return "a" if bucket(user_id) < 100 - b_percent() else "b"


@dataclass
class Style:
    text: str                  # 답 지시문에 넣을 길이 규칙 한 줄
    ref: str                   # 어디서 왔는지: "answer-style v2 (prod-b)" / "code(...)"
    client: object = None      # Langfuse 프롬프트 클라이언트(generation 연결용). 코드 문장이면 None
    variant: str = None        # a, b, 없으면 None


def valid_style(text):
    """길이 규칙은 '- '로 시작하는 한 줄이어야 한다. 여러 줄이거나 너무 길면 안전 규칙을 건드릴 수 있으니 쓰지 않는다."""
    return isinstance(text, str) and "\n" not in text.strip() and text.strip().startswith("- ") and 2 < len(text.strip()) <= MAX_STYLE_CHARS


def pick_style(user_id):
    variant = variant_for(user_id)
    fetched = None
    if variant:
        fetched = prompts.fetch_text_prompt(STYLE_PROMPT_NAME, STYLE_RULE, {}, label=GROUP_LABELS[variant])
    if fetched is None or fetched.client is None:       # A/B 가 꺼졌거나 그룹 라벨을 못 가져왔다
        fetched = prompts.fetch_text_prompt(STYLE_PROMPT_NAME, STYLE_RULE, {})
    if fetched.client is None:
        return Style(STYLE_RULE, fetched.ref, None, variant)
    text = fetched.text.strip()
    if not valid_style(text):
        log("WARNING", "가져온 길이 규칙이 한 줄 규칙이 아니라 코드의 문장을 씁니다.", prompt=STYLE_PROMPT_NAME)
        return Style(STYLE_RULE, f"code(한 줄 규칙이 아님: {fetched.ref})", None, variant)
    return Style(text, fetched.ref, fetched.client, variant)
