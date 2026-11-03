"""평가 데이터(data/eval/)를 Langfuse 데이터셋으로 올린다. N055 3장.

데이터셋 네 개: tool-selection(도구 선택), memory-extraction(기억 정리), safety(안전), holiday-reply(공휴일 답).
- 항목 id 를 '데이터셋이름-원본id'로 정해서, 같은 항목이 있으면 새로 만들지 않고 그 항목을 고친다.
  그래서 몇 번을 다시 실행해도 항목 수가 늘지 않는다(멱등).
- 키는 .env 의 LANGFUSE_* 에서 읽고 출력하지 않는다. 끝나면 데이터셋별 항목 수만 출력한다.

실행:
  python scripts/upload_datasets.py            # 올리기
  python scripts/upload_datasets.py --dry-run  # 올리지 않고 만들 항목 수만 출력
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EVAL = ROOT / "data" / "eval"


def read_jsonl(name):
    return [json.loads(line) for line in (EVAL / name).read_text(encoding="utf-8").splitlines() if line.strip()]


def build_tool_selection(row):
    return {"input": {"message": row["message"], "history": row["history"]},
            "expected_output": {"tools": row["expected_tools"], "optional_tools": row["optional_tools"],
                                "args": row["expected_args"]},
            "metadata": {"id": row["id"], "note": row.get("note", "")}}


def build_memory_extraction(row):
    return {"input": {"conversation": row["conversation"]},
            "expected_output": {"must_include": row["must_include"], "must_not_include": row["must_not_include"]},
            "metadata": {"id": row["id"]}}


def build_safety(row):
    return {"input": {"message": row["message"]},
            "expected_output": row["expected"],
            "metadata": {"id": row["id"], "category": row["category"]}}


def build_holiday_reply(row):
    return {"input": {"message": row["message"], "tool_result": row["tool_result"]},
            "expected_output": row["pass_condition"],
            "metadata": {"id": row["id"], "kind": row["kind"], "source": row.get("source", "")}}


DATASETS = [
    ("tool-selection", "tool_selection.jsonl", build_tool_selection, "도구 선택과 인자 평가"),
    ("memory-extraction", "memory_extraction.jsonl", build_memory_extraction, "기억 정리 평가"),
    ("safety", "safety.jsonl", build_safety, "안전 평가"),
    ("holiday-reply", "holiday_reply.jsonl", build_holiday_reply, "공휴일 답 평가(도구 결과를 고정해 넣음)"),
    ("holiday-reply-v2", "holiday_reply_v2.jsonl", build_holiday_reply,
     "공휴일 답 평가 v2: 같은 질문, 도구 결과는 연휴 계산·다음 공휴일을 넣은 새 get_holidays 문장(HR01·HR09 통과 조건만 수정)"),
]


def build_items(dataset_name, file_name, builder):
    """[(항목 id, input, expected_output, metadata)] — 항목 id 는 '데이터셋-원본id'로 고정."""
    items = []
    for row in read_jsonl(file_name):
        built = builder(row)
        items.append((f"{dataset_name}-{row['id']}", built["input"], built["expected_output"], built["metadata"]))
    return items


def main():
    dry = "--dry-run" in sys.argv
    plan = {name: build_items(name, file_name, builder) for name, file_name, builder, _ in DATASETS}
    if dry:
        for name, items in plan.items():
            print(f"{name}: {len(items)}개 (올리지 않음)")
        return

    from app.config import load_settings
    load_settings()   # .env 를 읽는다. 값은 출력하지 않는다
    from langfuse import Langfuse
    client = Langfuse()
    for name, _, _, description in DATASETS:
        client.create_dataset(name=name, description=description)
        for item_id, input_, expected, metadata in plan[name]:
            client.create_dataset_item(dataset_name=name, id=item_id, input=input_, expected_output=expected,
                                       metadata=metadata)
    client.flush()
    for name, _, _, _ in DATASETS:
        print(f"{name}: 항목 {len(client.get_dataset(name).items)}개 (보낸 {len(plan[name])}개)")


if __name__ == "__main__":
    main()
