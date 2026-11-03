"""답의 길이 규칙을 Langfuse 프롬프트 'answer-style' 두 버전으로 등록한다. N055 5장 A/B 테스트용.

- 버전 1: 지금 코드의 문장(persona.STYLE_RULE). production · staging · prod-a 라벨
- 버전 2: 같은 규칙을 더 짧게(1~2문장). prod-b 라벨
안전 규칙은 올리지 않는다(코드에 그대로 있다). 이미 있는 버전 번호는 만들지 않는다(다시 실행해도 늘지 않는다).
실행: python scripts/register_answer_style_prompt.py [--print]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ab import STYLE_PROMPT_NAME  # noqa: E402
from app.persona import STYLE_RULE  # noqa: E402

VERSIONS = {
    1: (STYLE_RULE, ["production", "staging", "prod-a"], "N055 버전 1: 지금 코드의 길이 규칙(1~4문장) — A 그룹·production"),
    2: ("- 답은 메신저 대화처럼 1~2문장으로 아주 짧게 쓴다.", ["prod-b"], "N055 버전 2: 1~2문장으로 더 짧게 — B 그룹"),
}


def main():
    if "--print" in sys.argv:
        for n, (text, labels, _) in VERSIONS.items():
            print(f"버전 {n} {labels}: {text}")
        return
    from app.config import load_settings
    load_settings()
    from langfuse import Langfuse
    client = Langfuse()
    for n, (text, labels, message) in VERSIONS.items():
        try:
            client.get_prompt(STYLE_PROMPT_NAME, version=n, type="text", max_retries=0)
            print(f"{STYLE_PROMPT_NAME} 버전 {n}: 이미 있음(만들지 않음)")
            continue
        except Exception:
            pass
        created = client.create_prompt(name=STYLE_PROMPT_NAME, type="text", prompt=text, labels=labels, commit_message=message)
        print(f"{STYLE_PROMPT_NAME} 버전 {n}: 등록됨(받은 번호 {created.version}, 라벨 {labels})")
        if created.version != n:
            print("!! 번호가 순서와 다릅니다. 중단합니다.")
            return


if __name__ == "__main__":
    main()
