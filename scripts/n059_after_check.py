"""N059 배포 뒤 확인: 데이터셋에 없는 새 약속 대화를 보내고, 말이 아니라 '저장소의 약속 목록'과 도구 호출로 통과 여부를 판정한다.

사용:
  로컬(수정 전 코드)  python after_check.py local <저장소 폴더> <출력.json> <version 이름>
  staging(배포 뒤)    python after_check.py remote <서비스 주소> <출력.json>
- 저장·조회·취소·일상(일정 이야기·기억 부탁)을 섞는다. 대화 원문은 출력 파일(스크래치패드)에만 쓰고 저장소에 올리지 않는다.
- remote 는 서비스의 /api/config 에 있는 공개 키로 시험용 게스트(익명)를 만들어 쓴다. 게스트 데이터는 staging 에 남는다.
- 판정(모두 코드로):
  save   약속이 정확히 1개 늘고, 새 약속의 날짜·시각이 기대와 같다
  list   list_reminders 도구가 불렸고 '저장·기억·조회할 수 없다'는 거짓 설명이 없다
  cancel 이름이 맞는 약속만 사라진다(없는 이름이면 아무것도 안 사라진다)
  normal 약속 수가 안 늘고 create_reminder 도구가 안 불렸다
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

mode, target, out = sys.argv[1], sys.argv[2], sys.argv[3]
KST = timezone(timedelta(hours=9))
now = datetime.now(KST)
today = now.date()


def nxt(wd):  # 오늘 이후 가장 가까운 요일(월=0)
    return today + timedelta(days=(wd - today.weekday()) % 7 or 7)


def week_day(next_week, wd):  # 이번 주/다음 주의 특정 요일
    return today - timedelta(days=today.weekday()) + timedelta(days=7 * next_week + wd)


def d(date_, hh, mm=0):
    return (date_.month, date_.day, hh, mm)


plus30 = now + timedelta(minutes=30)
SCEN = {
    "A 직장인": [
        ("save", "이번 주 금요일 저녁 7시에 팀 회식 있어, 기억해 줘", d(nxt(4), 19), "회식"),
        ("save", "12월 3일 오전 10시 건강검진 약속으로 넣어 줘", (12, 3, 10, 0), "건강검진"),
        ("list", "지금까지 저장해 둔 일정들 쭉 말해 줘"),
        ("normal", "다음 달에 부산으로 이사 가게 됐어"),
        ("save", "내일 오후 4시에 세무사 상담 등록해 줘", d(today + timedelta(days=1), 16), "세무사"),
        ("list", "내 약속 뭐뭐 있는지 읽어 봐"),
        ("cancel", "건강검진 약속 취소해 줘", "건강검진"),
        ("list", "남은 약속 알려 줘"),
        ("cancel", "치과 예약 지워 줘", None),
        ("normal", "내가 고양이를 키운다는 거 기억해 줘"),
    ],
    "B 학생": [
        ("save", "11월 28일 오후 2시에 동아리 발표 저장해 줘", (11, 28, 14, 0), "발표"),
        ("save", "모레 아침 7시 30분에 조깅 약속 잡아 줘", d(today + timedelta(days=2), 7, 30), "조깅"),
        ("normal", "오늘 학교에서 발표 준비하느라 힘들었어"),
        ("list", "내 일정 뭐 있는지 한번 읽어 줘"),
        ("cancel", "조깅 약속은 없던 걸로 해 줘", "조깅"),
        ("save", "다음 주 수요일 점심 12시에 교수님 면담 기억해 줘", d(week_day(1, 2), 12), "면담"),
        ("list", "저장된 약속 전부 알려 줘"),
        ("normal", "11월 30일이 내 생일이야"),
    ],
    "C 알림 부탁": [
        ("save", "30분 뒤에 전화하라고 알려 줘", ("now+30", plus30), "전화"),
        ("save", "내일 아침 9시에 약 먹으라고 알려 줘", d(today + timedelta(days=1), 9), "약"),
        ("list", "내 알림 뭐 있어?"),
        ("cancel", "약 먹는 알림 취소해 줘", "약"),
        ("normal", "내일 비 와?"),
        ("normal", "다음 공휴일 언제야?"),
    ],
    "D 면접": [
        ("normal", "오늘 너무 피곤해"),
        ("normal", "주말에 친구랑 영화 보기로 했어"),
        ("normal", "다음 주 목요일에 면접 있어서 떨려"),
        ("save", "그 면접 다음 주 목요일 오후 2시로 약속 저장해 줘", d(week_day(1, 3), 14), "면접"),
        ("list", "면접 일정 맞게 저장됐는지 확인해 줘"),
        ("cancel", "치과 약속은 취소할래", None),
    ],
}
CLAIM = re.compile(r"(저장|기억|등록|삭제|취소|조회|볼|알려\s*줄).{0,8}(수\s*없|못\s*해|못해)|기억하고 있는 약속은 없")


def label_parts(label):
    m = re.search(r"(\d+)월\s*(\d+)일", label)
    t = re.search(r"(\d{1,2}):(\d{2})", label)
    return (int(m.group(1)), int(m.group(2)), int(t.group(1)), int(t.group(2))) if m and t else None


if mode == "local":
    ROOT = target
    sys.path.insert(0, ROOT)
    os.chdir(ROOT)
    os.environ.update({"STORAGE": "memory", "AUTH": "none", "ENV": "local", "K_REVISION": sys.argv[4]})
    from fastapi.testclient import TestClient
    from app import observe
    from app.main import app
    client = TestClient(app)
    version = sys.argv[4]
else:
    http = httpx.Client(base_url=target, timeout=90)
    cfg = http.get("/api/config").json()
    r = httpx.post(cfg["supabase_url"] + "/auth/v1/signup", json={}, headers={"apikey": cfg["supabase_publishable_key"]}, timeout=30)
    r.raise_for_status()
    http.headers["Authorization"] = "Bearer " + r.json()["access_token"]
    client = http
    version = http.get("/version").json()["revision"]
    observe = None
    print("staging 리비전:", version, flush=True)


def pending():
    return [x for x in client.get("/api/reminders").json() if x["status"] == "pending"]


rows = []
for name, turns in SCEN.items():
    client.delete("/api/data")
    for i, t in enumerate(turns, 1):
        kind, text = t[0], t[1]
        before = pending()
        raw = client.post("/api/chat", json={"message": text}).text
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]
        reply = "".join(e.get("text", "") for e in events if e.get("type") == "delta")
        tools = [e.get("name") for e in events if e.get("type") == "tool"]
        turn_tools = next(([x["name"] for x in e.get("tools", [])] for e in events if e.get("type") == "turn"), [])
        after = pending()
        ids_before = {x["id"] for x in before}
        new = [x for x in after if x["id"] not in ids_before]
        gone = [x for x in before if x["id"] not in {y["id"] for y in after}]
        ok, why = False, ""
        if kind == "save":
            exp, key = t[2], t[3]
            if len(new) != 1:
                why = f"약속이 {len(new)}개 늘었다"
            else:
                got = label_parts(new[0]["due_label"])
                if exp[0] == "now+30":
                    want = exp[1]
                    ok = got is not None and (got[0], got[1]) == (want.month, want.day) and abs((got[2] * 60 + got[3]) - (want.hour * 60 + want.minute)) <= 5
                else:
                    ok = got == exp
                why = "" if ok else f"날짜·시각 불일치: 기대 {exp} 결과 {got}"
        elif kind == "list":
            ok = "list_reminders" in tools and not CLAIM.search(reply)
            why = "" if ok else ("조회 도구 안 불림" if "list_reminders" not in tools else "거짓 설명")
        elif kind == "cancel":
            key = t[2]
            if key is None:
                ok = not gone
                why = "" if ok else f"없는 이름인데 {[g['text'] for g in gone]} 취소됨"
            else:
                ok = len(gone) == 1 and key in gone[0]["text"]
                why = "" if ok else f"취소된 약속 {[g['text'] for g in gone]}"
        else:  # normal
            ok = not new and "create_reminder" not in turn_tools
            why = "" if ok else "일상 말에 약속이 저장됨"
        rows.append({"session": name, "turn": i, "kind": kind, "user": text, "reply": reply, "tools": tools, "ok": ok, "why": why,
                     "claim": bool(CLAIM.search(reply)), "pending_after": [x["text"] for x in after]})
        print(f"{name} {i:>2} {kind:6} {'OK ' if ok else 'FAIL'} {why}", flush=True)

if observe is not None:
    observe.flush()
json.dump({"version": version, "when": now.isoformat(), "rows": rows}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for k in ("save", "list", "cancel", "normal"):
    r = [x for x in rows if x["kind"] == k]
    print(f"{k}: {sum(x['ok'] for x in r)}/{len(r)}")
print("합계:", sum(x["ok"] for x in rows), "/", len(rows), "| 거짓 설명 표현이 든 답:", sum(x["claim"] for x in rows))
