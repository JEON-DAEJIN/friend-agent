"""N059: 약속 요청 평가. data/eval/n059_reminders.jsonl 의 문제 항목(P..)과 정상 항목(N..)을 따로 센다.
N055 평가 장치(choose() 전체 경로)를 그대로 쓴다. 사용법: python scripts/n059_eval.py [--repeat 3] [--out 결과.json]"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import eval_tools as ev  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.llm import make_llm  # noqa: E402
from app.router import choose  # noqa: E402

repeat = int(ev.option("--repeat") or 3)
out = ev.option("--out")
rows = [json.loads(line) for line in (ROOT / "data/eval/n059_reminders.jsonl").read_text(encoding="utf-8").splitlines()]
llm = make_llm(load_settings())
result = {"repeat": repeat, "model": llm.model, "runs": []}
for n in range(1, repeat + 1):
    r = ev.run_once(rows, choose, llm)
    passed = r["passed"]
    P = [x["id"] for x in rows if x["id"].startswith("P")]
    N = [x["id"] for x in rows if x["id"].startswith("N")]
    pp, nn = sum(i in passed for i in P), sum(i in passed for i in N)
    failed = sorted({f[0]["id"] for f in r["failures"]})
    detail = {f[0]["id"]: {"picked": f[1], "wrong": f[2]} for f in r["failures"]}
    print(f"{n}회차 문제 항목 {pp}/{len(P)}  정상 항목 {nn}/{len(N)}  틀린 항목: {', '.join(failed) or '없음'}")
    result["runs"].append({"problem": pp, "normal": nn, "failed": failed, "detail": detail})
if out:
    Path(out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
