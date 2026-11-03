"""holiday-reply 실험용 '답 지시문'을 Langfuse 프롬프트로 등록한다(버전 1). N055 3장.

서비스의 답 지시문(app/agent.py 의 system_prompt)을 기본 캐릭터 하루와 2026-11-02(월) 14:00 한국 시간으로 만든 뒤,
도구 결과 자리는 {{tool_result}}, 사용자 메시지 자리는 {{message}} 변수로 바꾼다.
변수 이름은 데이터셋 항목 input 의 키(message, tool_result)와 같아야 실험에서 값이 채워진다.
같은 이름의 프롬프트가 이미 있으면 새 버전을 만들지 않는다(--new-version 을 줄 때만 만든다).

실행: python scripts/register_holiday_reply_prompt.py [--print] [--new-version]
"""
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent import system_prompt  # noqa: E402
from app.persona import DEFAULT_CHARACTER  # noqa: E402

PROMPT_NAME = "holiday-reply-answer"
NOW = datetime(2026, 11, 2, 14, 0, tzinfo=ZoneInfo("Asia/Seoul"))
LOCATION = "서울"


def build_messages():
    system = system_prompt(DEFAULT_CHARACTER, NOW, LOCATION, [], "", tool_lines=("{{tool_result}}",))
    return [{"role": "system", "content": system}, {"role": "user", "content": "{{message}}"}]


def main():
    messages = build_messages()
    if "--print" in sys.argv:
        for m in messages:
            print(f"[{m['role']}]\n{m['content']}")
        return
    from app.config import load_settings
    load_settings()
    from langfuse import Langfuse
    client = Langfuse()
    exists = False
    try:
        client.get_prompt(PROMPT_NAME, label="latest", type="chat", max_retries=0)
        exists = True
    except Exception:   # 없으면 오류가 난다
        pass
    if exists and "--new-version" not in sys.argv:
        print(f"{PROMPT_NAME}: 이미 있어서 새 버전을 만들지 않았습니다(--new-version 으로 만들 수 있음)")
        return
    created = client.create_prompt(name=PROMPT_NAME, type="chat", prompt=messages, labels=[],
                                   commit_message="N055 holiday-reply 실험용 답 지시문 버전 1")
    print(f"{PROMPT_NAME}: 버전 {created.version} 등록, 변수 {sorted(created.variables) if hasattr(created, 'variables') else '?'}")


if __name__ == "__main__":
    main()
