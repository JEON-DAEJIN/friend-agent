"""Langfuse 프롬프트 관리에서 지시문을 가져온다. N055 4장.

- 라벨로 가져온다: ENV 가 production 이면 production 라벨, 그 밖에는 staging 라벨. 평가·실험에서는 라벨이나 버전 번호를 직접 정한다.
- Langfuse 가 꺼져 있거나 닿지 못하면 코드에 남겨 둔 지시문(fallback)으로 답한다. 그래서 Langfuse 가 멈춰도 서비스는 멈추지 않는다.
- 가져온 프롬프트 클라이언트를 함께 돌려줘서, 호출하는 쪽이 generation 에 연결(어느 이름·버전으로 답했는지 트레이스에 남김)할 수 있다.
"""
import os
import time
from dataclasses import dataclass

from app import observe
from app.logs import log

FETCH_TIMEOUT_SECONDS = 3   # Langfuse 에 닿지 못할 때 메시지 하나가 기다리는 시간의 상한(SDK 기본값은 5초)
FETCH_RETRIES = 0
COOLDOWN_SECONDS = 60       # 닿지 못했으면 이 시간 동안은 묻지 않고 곧장 코드의 지시문으로 답한다(메시지마다 기다리지 않게)
_down_until = {}              # 프롬프트 이름 → 이 시각(time.monotonic)까지는 묻지 않는다


@dataclass
class Fetched:
    text: str                  # 변수를 채운 완성 지시문
    ref: str                   # 어디서 왔는지: "tool-router v3 (production)" / "code(...)"
    client: object = None      # Langfuse 프롬프트 클라이언트. generation 연결용. 코드 지시문이면 None


def default_label():
    return "production" if (os.environ.get("ENV") or "local") == "production" else "staging"


def render(template, **variables):
    for key, value in variables.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def fetch_text_prompt(name, fallback, variables, label=None, version=None):
    """name 프롬프트(text)를 version 이 있으면 그 번호로, 없으면 label(기본은 ENV 에 맞는 라벨)로 가져와 variables 를 채운다."""
    client = observe.get_client()
    if client is None:
        return Fetched(render(fallback, **variables), "code(Langfuse 꺼짐)")
    if time.monotonic() < _down_until.get(name, 0.0):
        return Fetched(render(fallback, **variables), "code(Langfuse 에 닿지 못해 잠시 쉬는 중)")
    chosen = f"v{version}" if version else (label or default_label())
    try:
        selector = {"version": version} if version else {"label": label or default_label()}
        # SDK 의 fallback= 을 쓰지 않는다. 쓰면 '없는 라벨'과 'Langfuse 에 닿지 못함'이 둘 다 fallback 으로 돌아와 구분이 안 된다.
        prompt = client.get_prompt(name, type="text", max_retries=FETCH_RETRIES,
                                   fetch_timeout_seconds=FETCH_TIMEOUT_SECONDS, **selector)
        text = prompt.compile(**variables)
    except Exception as error:
        if type(error).__name__ == "NotFoundError":   # 그 이름·라벨의 프롬프트가 없다. Langfuse 는 살아 있으니 쉬지 않는다(다른 라벨을 시도할 수 있다)
            log("WARNING", "그 프롬프트(라벨·버전)가 없어 코드의 지시문으로 답합니다.", prompt=name, requested=chosen)
            return Fetched(render(fallback, **variables), f"code(없음: {name} {chosen})")
        _down_until[name] = time.monotonic() + COOLDOWN_SECONDS
        log("WARNING", "프롬프트를 가져오지 못해 코드의 지시문으로 답합니다.", prompt=name, error=type(error).__name__)
        return Fetched(render(fallback, **variables), f"code(가져오기 실패: {type(error).__name__})")
    if getattr(prompt, "is_fallback", False):
        _down_until[name] = time.monotonic() + COOLDOWN_SECONDS
        log("WARNING", "Langfuse 에 닿지 못해 코드의 지시문으로 답합니다.", prompt=name)
        return Fetched(text, f"code(Langfuse 에 닿지 못함, 요청 {chosen})")
    return Fetched(text, f"{name} v{prompt.version} ({chosen})", prompt)
