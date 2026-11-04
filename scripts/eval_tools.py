"""도구 선택 평가. data/eval/tool_selection.jsonl 의 메시지마다 고른 도구와 핵심 인자가 정답과 맞는지 센다.

실행:
  python scripts/eval_tools.py            # .env 의 LLM 으로 모델 선택기를 평가 (호출 40번, 비용 발생)
  python scripts/eval_tools.py --rule     # 키워드 규칙 선택기를 평가 (비용 없음)

정답의 날짜와 시각은 '2026-10-12(월) 14:00 한국 시간'을 지금으로 보고 만들었다. 평가도 그 시각을 지금으로 쓴다.
"""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import load_settings  # noqa: E402
from app.llm import make_llm  # noqa: E402
from app.router import choose, model_choose, rule_choose  # noqa: E402

NOW = datetime.fromisoformat("2026-10-12T14:00:00+09:00")
LOCATION = "서울"


def _date(text):
    try:
        return date.fromisoformat(str(text)[:10]) if text else None
    except ValueError:
        return None


def args_match(expected, actual):
    """정답에 적힌 인자만 비교한다. due_at 은 분 단위까지 같은 시각이면 맞다."""
    wrong = []
    # 조회 기간이 날짜를 포함하는지만 본다. 인자를 비우면 도구가 오늘부터 1년을 찾으므로 그것도 정답이다(N055).
    start = _date(actual.get("from")) or NOW.date()
    end = _date(actual.get("to")) or NOW.date() + timedelta(days=365)
    for key, want in expected.items():
        got = actual.get(key)
        if key == "next_holiday":
            if not (start <= date.fromisoformat(want) <= end):
                wrong.append(f"기간 {start}~{end} 에 다음 공휴일 {want} 이(가) 없음")
            continue
        if key == "covers":
            if not (start <= date.fromisoformat(want[0]) and date.fromisoformat(want[1]) <= end):
                wrong.append(f"기간 {start}~{end} 이(가) {want[0]}~{want[1]} 를 다 덮지 못함")
            continue
        if key == "due_at":
            try:
                same = datetime.fromisoformat(str(got)).replace(tzinfo=None, second=0) == datetime.fromisoformat(want)
            except ValueError:
                same = False
        elif key in ("from", "to"):
            same = str(got or "")[:7] == want[:7]   # 월까지만 비교
        elif key == "day":   # 정답은 '오늘로부터 며칠 뒤', 결과는 date(YYYY-MM-DD)
            try:
                same = (datetime.fromisoformat(str(actual.get("date"))).date() - NOW.date()).days == want
            except ValueError:
                same = False
            got = actual.get("date")
        else:
            same = want in str(got or "")
        if not same:
            wrong.append(f"{key}: 정답 {want}, 결과 {got}")
    return wrong


def option(name):
    """--label production 처럼 값을 받는 옵션. 없으면 None."""
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None


def load_rows():
    return [json.loads(line) for line in (ROOT / "data/eval/tool_selection.jsonl").read_text(encoding="utf-8").splitlines()]


def run_once(rows, pick, llm, label=None, version=None, use_rule=False):
    """41개를 한 번 평가한다. {tool_ok, args_ok, n, passed(도구와 인자가 모두 맞은 항목 id), failures, used}."""
    tool_ok = args_ok = 0
    passed, failures, used = set(), [], ""
    for row in rows:
        if use_rule:
            choice = rule_choose(row["message"], NOW, LOCATION)
        else:
            choice = pick(row["message"], row["history"], NOW, LOCATION, llm, label=label, version=version)
            used = choice.prompt
        picked = {t["name"] for t in choice.tools}
        required, optional = set(row["expected_tools"]), set(row["optional_tools"])
        tools_right = required <= picked <= required | optional
        wrong = []
        if tools_right:
            tool_ok += 1
            got = {t["name"]: t["args"] for t in choice.tools}
            for name, expected in row["expected_args"].items():
                wrong += args_match(expected, got.get(name, {}))
            if not wrong:
                args_ok += 1
                passed.add(row["id"])
        if not tools_right or wrong:
            failures.append((row, sorted(picked), wrong, choice.confidence))
    return {"tool_ok": tool_ok, "args_ok": args_ok, "n": len(rows), "passed": passed, "failures": failures, "used": used}


def gate(runs, min_exact=None, must_pass=()):
    """CI 평가 게이트의 판정(N055 6장). LLM 을 부르지 않는 순수 함수.
    - min_exact: 모든 회차에서 '도구와 인자 모두 정답'이 이 값 이상이어야 한다(한 회차라도 못 미치면 실패)
    - must_pass: 이 항목들은 모든 회차에서 정답이어야 한다
    돌려주는 값: (통과 여부, 실패 이유 목록)."""
    reasons = []
    for number, run in enumerate(runs, 1):
        if min_exact is not None and run["args_ok"] < min_exact:
            reasons.append(f"{number}회차: 도구와 인자 모두 정답 {run['args_ok']}개가 기준 {min_exact}개보다 적다")
        for item in must_pass:
            if item not in run["passed"]:
                reasons.append(f"{number}회차: 꼭 맞아야 하는 항목 {item} 이(가) 틀렸다")
    return not reasons, reasons


def main():
    use_rule = "--rule" in sys.argv
    full = "--full" in sys.argv   # 서비스가 실제로 거치는 choose() 전체(코드 안전장치 포함)로 평가한다. 없으면 모델 선택만
    label = option("--label")                                  # Langfuse 프롬프트 tool-router 의 라벨(production·staging 등)
    version = int(option("--version")) if option("--version") else None   # 라벨이 없는 버전은 번호로 고른다. 번호가 우선
    repeat = int(option("--repeat") or 1)                      # 몇 번 돌릴지. 실행마다 한두 개씩 흔들리므로 게이트는 여러 번 돌린다
    min_exact = int(option("--min-exact")) if option("--min-exact") else None   # 기준 미달이면 종료 코드 1(CI 게이트)
    must_pass = [x for x in (option("--must-pass") or "").split(",") if x]       # 예: T34,T26
    llm = None if use_rule else make_llm(load_settings())
    if llm is not None and not llm.can_reason:
        print("LLM_PROVIDER=fake 에서는 모델 평가를 할 수 없습니다. --rule 로 실행하세요.")
        sys.exit(2 if min_exact is not None else 0)   # 게이트가 평가를 못 했으면 통과로 치지 않는다

    rows = load_rows()
    pick = choose if full else model_choose
    runs = []
    for number in range(1, repeat + 1):
        result = run_once(rows, pick, llm, label, version, use_rule)
        runs.append(result)
        prefix = f"{number}회차 " if repeat > 1 else ""
        print(f"{prefix}[{'키워드 규칙' if use_rule else '모델 ' + llm.model}] 도구 정답 {result['tool_ok']}/{result['n']}, "
              f"도구와 인자 모두 정답 {result['args_ok']}/{result['n']}"
              + (f"  (지시문: {result['used']})" if result["used"] else "") + ("  [choose() 전체 경로]" if full else ""))
        for row, picked, wrong, conf in result["failures"]:
            detail = "; ".join(wrong) if wrong else f"정답 {row['expected_tools'] or '도구 없음'}, 결과 {picked or '도구 없음'}"
            print(f"  {row['id']} {row['message']}  →  {detail}  (확신도 {conf:.2f})")

    if min_exact is not None or must_pass:
        ok, reasons = gate(runs, min_exact, must_pass)
        print(f"평가 게이트: {'통과' if ok else '실패'} (기준: 모두 정답 {min_exact}개 이상, 꼭 맞아야 하는 항목 {must_pass or '없음'}, {repeat}회)")
        for reason in reasons:
            print("  -", reason)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
