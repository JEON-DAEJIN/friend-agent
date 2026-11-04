"""도구 선택 지시문(router_prompt)을 Langfuse 프롬프트 'tool-router' 세 버전으로 등록한다. N055 4장.

- 버전 1: 최초 커밋의 지시문 = 지금 코드에서 N054가 더한 공휴일 두 줄을 뺀 것(교안의 버전 1)
- 버전 2: 버전 1 + 교안의 두 규칙(기간 해석, 전날 계산) — confidence 줄 바로 앞에 더한다(교안의 버전 2)
- 버전 3: 지금 코드 그대로(버전 1 + N054 공휴일 두 줄, 전날 규칙 없음). production 라벨을 붙인다(서비스 동작을 바꾸지 않는다)
도구 목록은 {{tools}} 변수로 둔다. 버전 2에는 staging 라벨을 붙인다.
이미 있는 버전 번호는 만들지 않는다(다시 실행해도 늘지 않는다). 실행: python scripts/register_router_prompt.py [--print]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import router  # noqa: E402
from app.tools import TOOLS  # noqa: E402

PROMPT_NAME = "tool-router"

HOLIDAY_START = ("- '다음 공휴일', ", "- get_holidays 의 from 과 to ")      # N054 가 더한 두 줄의 시작
LECTURE_RULES = [
    "- get_holidays 는 '12월', '설날'처럼 기간을 말할 때만 from, to 를 채운다. '다음 공휴일', '남은 빨간 날'처럼 기간이 없으면 인자를 비워 둔다.",
    "- '시험 전날', '출발 이틀 전'처럼 어떤 날을 기준으로 앞뒤를 말하면, 먼저 [달력]에서 기준이 되는 날의 날짜를 찾고 거기서 날짜를 빼거나 더한다.",
]


def tools_block():
    return "\n".join(f"- {name}: {t['desc']}. 인자 {t['args']}" for name, t in TOOLS.items())


def build_versions():
    """{버전 번호: 지시문}. 도구 목록 자리는 {{tools}}."""
    current = router.router_prompt().replace(tools_block(), "{{tools}}")
    assert current.count("{{tools}}") == 1 and current.count("{{") == 1
    lines = current.split("\n")
    v3 = current
    v1_lines = [l for l in lines if not l.startswith(HOLIDAY_START)]
    assert len(lines) - len(v1_lines) == 2
    at = next(i for i, l in enumerate(v1_lines) if l.startswith("- confidence "))
    v2_lines = v1_lines[:at] + LECTURE_RULES + v1_lines[at:]
    return {1: "\n".join(v1_lines), 2: "\n".join(v2_lines), 3: v3}


LABELS = {1: [], 2: ["staging"], 3: ["production"]}
COMMITS = {1: "N055 버전 1: 최초 커밋의 도구 선택 지시문(N054 공휴일 줄 이전)",
           2: "N055 버전 2: 버전 1 + 교안의 기간·전날 규칙 두 줄",
           3: "N055 버전 3: 지금 코드 그대로(N054 공휴일 두 줄 포함) — production"}


def main():
    versions = build_versions()
    if "--print" in sys.argv:
        for n, text in versions.items():
            print(f"=== 버전 {n} ({len(text.splitlines())}줄, 라벨 {LABELS[n]}) ===\n{text}\n")
        return
    from app.config import load_settings
    load_settings()
    from langfuse import Langfuse
    client = Langfuse()
    for n in (1, 2, 3):
        try:
            client.get_prompt(PROMPT_NAME, version=n, type="text", max_retries=0)
            print(f"{PROMPT_NAME} 버전 {n}: 이미 있음(만들지 않음)")
            continue
        except Exception:
            pass
        created = client.create_prompt(name=PROMPT_NAME, type="text", prompt=versions[n], labels=LABELS[n],
                                       commit_message=COMMITS[n])
        print(f"{PROMPT_NAME} 버전 {n}: 등록됨(받은 번호 {created.version}, 라벨 {LABELS[n]})")
        if created.version != n:
            print("!! 번호가 순서와 다릅니다. 중단합니다.")
            return


if __name__ == "__main__":
    main()
