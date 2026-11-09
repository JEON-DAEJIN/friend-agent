"""N059: 가상 사용자 대본(n059_scenarios.py)을 서비스 코드에 그대로 보내 트레이스를 만들고, 질문·답을 JSON 으로 저장한다.

- 서비스 코드를 이 저장소 그대로 돌린다(노트북, 메모리 저장소, 로그인 없음). 그래서 이 폴더가 가리키는 커밋이 곧 시험 대상 버전이다.
- 유형 하나가 끝날 때마다 DELETE /api/data 로 대화·기억·약속을 비워, 앞 유형의 대화가 다음 유형에 섞이지 않게 한다.
- 대화 원문은 저장소 밖(출력 경로)에만 저장한다. 사용법: python scripts/n059_collect.py <출력.json> [version 이름]
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.update({"STORAGE": "memory", "AUTH": "none", "ENV": "local"})
if len(sys.argv) > 2:
    os.environ["K_REVISION"] = sys.argv[2]          # 트레이스의 version 칸에 들어간다(observe.trace_attrs)

from fastapi.testclient import TestClient  # noqa: E402

from app import observe  # noqa: E402
from app.main import app, store  # noqa: E402
from scripts.n059_scenarios import SCENARIOS  # noqa: E402

out = Path(sys.argv[1])
client = TestClient(app)
started = datetime.now(timezone.utc).isoformat()
rows = []
for kind, messages in SCENARIOS.items():
    client.delete("/api/data")
    for i, text in enumerate(messages, 1):
        t0 = time.time()
        raw = client.post("/api/chat", json={"message": text}).text
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]
        done = next((e for e in events if e.get("type") == "done"), {})
        reply = "".join(e.get("text", "") for e in events if e.get("type") in ("token", "delta"))
        mid = done.get("message_id")
        meta = (store.get_message("local-user", mid) or {}).get("meta", {}) if mid else {}
        saved = store.get_message("local-user", mid) if mid else {}
        rows.append({"kind": kind, "turn": i, "user": text, "reply": reply or saved.get("content", ""),
                     "message_id": mid, "trace_id": meta.get("trace_id"), "seconds": round(time.time() - t0, 2),
                     "tools": [e.get("name") for e in events if e.get("type") == "tool"],
                     "tool_ok": [e.get("ok") for e in events if e.get("type") == "tool"],
                     "reminder": [e.get("summary") for e in events if e.get("type") == "reminder"],
                     "event_types": sorted({e.get("type") for e in events})})
    print(f"{kind}: {len(messages)}턴 끝", flush=True)

observe.flush()
json.dump({"started": started, "version": os.environ.get("K_REVISION", "local"), "rows": rows},
          out.open("w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("저장:", out, "| 총", len(rows), "턴")
