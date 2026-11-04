"""평가 게이트 워크플로(.github/workflows/eval.yml)의 안전 약속을 지킨다(N055 6장). 글자로 검사하므로 GitHub 없이 돈다.

- PR 마다 자동으로 돌지 않는다(라벨이 붙었을 때와 버튼뿐). 비용이 드는 실제 LLM 을 부르기 때문이다.
- pull_request_target 을 쓰지 않는다(외부 코드가 시크릿으로 돌지 않게). 쓰기 권한이 없다.
- 시크릿은 값을 찍지 않고, 게이트 옵션(기준·꼭 맞아야 하는 항목)이 들어 있다.
"""
import re
from pathlib import Path

WORKFLOW = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "eval.yml").read_text(encoding="utf-8")
CODE = "\n".join(line for line in WORKFLOW.splitlines() if not line.lstrip().startswith("#"))


def test_it_only_runs_on_a_label_or_the_manual_button():
    assert "types: [labeled]" in CODE and "workflow_dispatch:" in CODE
    assert "event.label.name == 'eval'" in CODE
    assert "push:" not in CODE and "schedule:" not in CODE


def test_it_does_not_run_external_code_with_secrets_or_write_access():
    assert "pull_request_target" not in CODE
    assert "head.repo.full_name == github.repository" in CODE          # 포크 PR 은 건너뛴다
    assert re.search(r"permissions:\s*\n\s+contents: read", CODE) and "write" not in CODE


def test_the_secret_is_never_printed_and_only_the_eval_step_gets_it():
    assert CODE.count("secrets.LLM_API_KEY") == 1
    assert 'echo "$LLM_API_KEY' not in CODE and "echo $LLM_API_KEY" not in CODE
    assert "LANGFUSE_TRACING_ENABLED" in CODE and "LANGFUSE_SECRET_KEY" not in CODE


def test_the_gate_options_are_there_and_the_threshold_is_a_number():
    m = re.search(r"--min-exact (\d+)", CODE)
    assert m and 1 <= int(m.group(1)) <= 41
    assert "--must-pass T34" in CODE and "--repeat 2" in CODE and "--full" in CODE
    assert "pipefail" in CODE                                          # tee 를 거쳐도 실패 종료 코드가 살아 있다
