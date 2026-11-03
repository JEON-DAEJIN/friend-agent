"""도구 선택 지시문(tool-router)의 버전을 Langfuse 데이터셋 tool-selection 으로 실험한다. N055 4장.

버전마다 41개 전체를 반복해서 돌리고, 실행 하나가 Langfuse Datasets > tool-selection > Experiments 에 한 줄로 남는다.
평가는 scripts/eval_tools.py 와 같은 기준(고른 도구가 맞는가 / 도구와 인자가 모두 맞는가)이고, 같은 NOW(2026-10-12 14:00)를 쓴다.
모델 호출은 항목 수 × 버전 수 × 반복 수 번이라 비용이 든다. 먼저 --local N 으로 N개만 Langfuse 없이 시험해 볼 수 있다.

실행:
  python scripts/run_tool_experiment.py --versions 1 2 3 --repeat 2 [--dump 파일.json]
  python scripts/run_tool_experiment.py --local 3 --versions 3        # Langfuse 에 기록하지 않고 앞 N개만
실행 이름: router-v<버전>-<회차> (같은 이름이 이미 있으면 --force 없이는 건너뛴다)
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import eval_tools  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.llm import make_llm  # noqa: E402
from app.router import model_choose  # noqa: E402

DATASET = "tool-selection"
EXPERIMENT = "tool-router-compare"


def option(name, default=None, many=False):
    if name not in sys.argv:
        return default
    start = sys.argv.index(name) + 1
    values = []
    for token in sys.argv[start:]:
        if token.startswith("--"):
            break
        values.append(token)
        if not many:
            break
    return values if many else (values[0] if values else default)


def judge(expected, choice):
    """(도구가 맞는가, 인자까지 모두 맞는가, 틀린 이유 목록). eval_tools.main 과 같은 기준."""
    picked = {t["name"] for t in choice["tools"]}
    required, optional = set(expected["tools"]), set(expected["optional_tools"])
    tools_right = required <= picked <= required | optional
    wrong = []
    if tools_right:
        got = {t["name"]: t["args"] for t in choice["tools"]}
        for name, want in expected["args"].items():
            wrong += eval_tools.args_match(want, got.get(name, {}))
    return tools_right, tools_right and not wrong, wrong


def make_task(llm, version):
    def task(*, item, **kwargs):
        data = item.input if hasattr(item, "input") else item["input"]
        choice = model_choose(data["message"], data["history"], eval_tools.NOW, eval_tools.LOCATION, llm, version=version)
        return {"tools": choice.tools, "confidence": choice.confidence, "prompt": choice.prompt}
    return task


def evaluators():
    from langfuse import Evaluation

    def tools_right(*, input, output, expected_output, **kwargs):
        right, _, _ = judge(expected_output, output)
        picked = ",".join(t["name"] for t in output["tools"]) or "도구 없음"
        return Evaluation(name="tools-right", value=1 if right else 0, comment=f"고른 도구: {picked}")

    def exact(*, input, output, expected_output, **kwargs):
        _, ok, wrong = judge(expected_output, output)
        return Evaluation(name="tools-and-args-right", value=1 if ok else 0, comment="; ".join(wrong)[:300] or None)

    return [tools_right, exact]


def local(count, versions):
    llm = make_llm(load_settings())
    rows = [json.loads(line) for line in (ROOT / "data/eval/tool_selection.jsonl").read_text(encoding="utf-8").splitlines()][:count]
    for version in versions:
        task = make_task(llm, version)
        for row in rows:
            item = {"input": {"message": row["message"], "history": row["history"]}}
            out = task(item=item)
            expected = {"tools": row["expected_tools"], "optional_tools": row["optional_tools"], "args": row["expected_args"]}
            right, ok, wrong = judge(expected, out)
            print(f"v{version} {row['id']} 도구 {'O' if right else 'X'} 인자까지 {'O' if ok else 'X'} {wrong or ''} ← {out['prompt']}")


def main():
    versions = [int(v) for v in option("--versions", ["1", "2", "3"], many=True)]
    if option("--local"):
        local(int(option("--local")), versions)
        return
    repeat = int(option("--repeat", "2"))
    dump_path = option("--dump")
    llm = make_llm(load_settings())
    from langfuse import Langfuse

    client = Langfuse()
    dataset = client.get_dataset(DATASET)
    print(f"{DATASET}: 항목 {len(dataset.items)}개, 버전 {versions} × {repeat}회")
    dump = {}
    for version in versions:
        for run in range(1, repeat + 1):
            run_name = f"router-v{version}-{run}"
            result = dataset.run_experiment(name=EXPERIMENT, run_name=run_name,
                                            description=f"tool-router 버전 {version}, {run}회차 (N055)",
                                            task=make_task(llm, version), evaluators=evaluators(), max_concurrency=4,
                                            metadata={"prompt_version": str(version), "run": str(run)})
            rows = {}
            for r in result.item_results:
                scores = {e.name: e.value for e in r.evaluations}
                rows[r.item.metadata["id"]] = {"tools_right": scores.get("tools-right"), "exact": scores.get("tools-and-args-right"),
                                               "output": r.output, "comments": [e.comment for e in r.evaluations if e.comment]}
            dump[run_name] = rows
            tools = sum(1 for v in rows.values() if v["tools_right"] == 1)
            exact = sum(1 for v in rows.values() if v["exact"] == 1)
            print(f"{run_name}: 도구 정답 {tools}/{len(rows)}, 도구와 인자 모두 정답 {exact}/{len(rows)}")
    client.flush()
    if dump_path:
        Path(dump_path).write_text(json.dumps(dump, ensure_ascii=False, indent=1), encoding="utf-8")
        print("saved:", dump_path)


if __name__ == "__main__":
    main()
