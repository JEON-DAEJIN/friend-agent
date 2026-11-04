"""Langfuse 관측(트레이스)을 한곳에 모은다.

메시지 하나(chat-turn)를 트레이스로 묶고, 단계마다 관찰 단위를 남긴다. 단계 이름·종류는 docs 의 계측 구조표를 그대로 따른다.
  chat-turn(트레이스) · load-context(span) · safety(span) · choose-tools(generation) · tool:<이름>(span) · reply(generation)
  save-turn(span) · memory-update(generation) · summary-check(span) · memory-summary(generation) · greeting(트레이스) · greeting-reply(generation)
  (load-context·save-turn·summary-check 는 6장 실습에서 총 시간의 절반 가까이가 이름 없는 구간이라 따로 보이게 더한 저장소 접근 단계)

규칙
- 키(LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY)가 없거나 LANGFUSE_TRACING_ENABLED=false 면 계측을 끄고 앱은 그대로 돈다.
- 관측 도구가 고장 나도 대화는 멈추지 않는다. 계측 쪽 오류는 삼키고 경고 로그 한 줄만 남긴다.
- 입력·출력 원문은 남기되, 보내기 전에 이메일·카드 번호·전화번호 패턴을 가린다(mask).
- 서버 로그(logs.py)에는 대화 원문을 남기지 않는다. 원문은 이 관측 도구에만 간다.
- 응답 도중 끊겨도 부모·자식이 이어지게, 데코레이터의 자동 중첩 대신 부모 관찰 단위를 직접 들고 다닌다
  (run_turn 은 중간중간 멈췄다 이어지는 제너레이터라서 실행 문맥이 끊길 수 있다).
"""
import inspect
import os
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.logs import log

# ── 마스킹 ──────────────────────────────────────────────────────────

MASK_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[이메일]"),
    (re.compile(r"(?<!\d)\d{6}\s?-\s?[1-4]\d{6}(?!\d)"), "[주민번호]"),     # 카드 모양(숫자 13자리)보다 먼저 가려야 제 이름이 붙는다
    (re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"), "[카드번호]"),
    (re.compile(r"(?<!\d)0\d{1,2}[-. ]?\d{3,4}[-. ]?\d{4}(?!\d)"), "[전화번호]"),
    # 계좌번호(예: 110-123-456789). 날짜(2026-11-03)가 가려지지 않게 마지막 묶음이 4자리 이상일 때만 본다. 전화번호를 먼저 가린 뒤에 본다
    (re.compile(r"(?<!\d)\d{3,6}-\d{2,6}-\d{4,8}(?!\d)"), "[계좌번호]"),
    # '비밀번호는 abc1234야'처럼 말 바로 뒤의 값. 말은 남기고 값만 가린다. 값에 숫자나 영문자가 있어야 한다('비밀번호 잊어버렸어'는 그대로)
    (re.compile(r"(비밀번호|패스워드|passwd|password)(\s*(?:은|는|이|가|:|=)?\s*)(?=[^\s,]*[A-Za-z0-9])[^\s,]{2,}", re.I), r"\1\2[비밀번호]"),
]


def mask(*, data, **kwargs):
    """Langfuse 로 보내기 전에 문자열 속 개인정보 패턴을 가린다. 사전·목록 안쪽까지 들어가서 바꾼다."""
    if isinstance(data, str):
        for pattern, label in MASK_PATTERNS:
            data = pattern.sub(label, data)
        return data
    if isinstance(data, dict):
        return {key: mask(data=value) for key, value in data.items()}
    if isinstance(data, (list, tuple)):
        return [mask(data=value) for value in data]
    return data


# ── 끄기 ────────────────────────────────────────────────────────────

class _Noop:
    """계측이 꺼졌을 때 쓰는 빈 관찰 단위. 어떤 호출을 해도 아무 일도 하지 않는다."""
    trace_id = None

    def start_observation(self, **kwargs):
        return self

    def tag(self, *tags):
        return self

    def update(self, **kwargs):
        return self

    def end(self, **kwargs):
        return None


NOOP = _Noop()

_client = None
_client_ready = False
_lock = threading.Lock()


def _enabled():
    if os.environ.get("LANGFUSE_TRACING_ENABLED", "").strip().lower() in ("0", "false", "no"):
        return False
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))


def get_client():
    """Langfuse 클라이언트를 처음 쓸 때 한 번 만든다. 꺼져 있거나 만들지 못하면 None."""
    global _client, _client_ready
    if _client_ready:
        return _client
    with _lock:
        if _client_ready:
            return _client
        if _enabled():
            try:
                from langfuse import Langfuse

                _client = Langfuse(base_url=os.environ.get("LANGFUSE_BASE_URL") or None, mask=mask)
                log("INFO", "Langfuse 관측을 켰습니다.")
            except Exception as error:
                log("WARNING", "Langfuse 를 시작하지 못해 관측 없이 돌립니다.", error=type(error).__name__)
                _client = None
        _client_ready = True
    return _client


def set_client_for_test(client):
    """테스트에서 가짜 클라이언트를 끼워 넣는다. None 이면 꺼진 상태."""
    global _client, _client_ready
    _client, _client_ready = client, True


def trace_attrs(uid):
    """트레이스에 붙일 속성. user_id 는 로그인으로 확인한 사용자 ID(이메일 아님), session_id 는 사용자 ID + 한국 날짜."""
    day = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    return {
        "user_id": str(uid)[:200],
        "session_id": f"{uid}:{day}"[:200],
        "environment": os.environ.get("ENV") or "local",     # local, staging, production
        "version": os.environ.get("K_REVISION") or "local",  # Cloud Run 이 넣어 주는 리비전 이름
        "metadata": {"commit": (os.environ.get("COMMIT_SHA") or "local")[:200]},
    }


class Handle:
    """관찰 단위 하나와 트레이스 속성을 함께 들고 다닌다.

    속성(user_id, session_id, environment, version, tags, metadata)은 propagate_attributes 안에서 만든 관찰 단위에만 붙는다.
    run_turn 은 멈췄다 이어지는 제너레이터라 with 문을 길게 열어 둘 수 없으므로, 관찰 단위를 만드는 그 순간에만 열어서 붙인다.
    (일회용 시험: 안에서 만든 루트는 속성이 붙고, 밖에서 만든 자식은 environment 외에는 비어 있었다.)
    """

    def __init__(self, obs, attrs):
        self._obs = obs
        self.attrs = attrs  # 같은 트레이스의 관찰 단위들이 하나를 같이 쓴다. 나중에 tag() 로 태그를 더하면 그 뒤에 만드는 것부터 붙는다.

    @property
    def trace_id(self):
        return getattr(self._obs, "trace_id", None)

    def tag(self, *tags):
        merged = list(self.attrs.get("tags") or [])
        merged += [t for t in tags if t and t not in merged]
        self.attrs["tags"] = merged[:20]
        return self

    def start_observation(self, **kwargs):
        from langfuse import propagate_attributes

        with propagate_attributes(**self.attrs):
            return Handle(self._obs.start_observation(**kwargs), self.attrs)

    def update(self, **fields):
        self._obs.update(**fields)
        return self

    def end(self, **kwargs):
        if getattr(self, "_ended", False):  # 같은 관찰 단위를 두 번 닫으면 SDK 가 경고한다
            return None
        self._ended = True
        return self._obs.end(**kwargs)


def start_trace(name, input=None, user_id=None):
    """트레이스의 가장 바깥 관찰 단위를 연다. 끝낼 때는 end() 를 부른다. user_id 를 주면 속성을 붙인다."""
    client = get_client()
    if client is None:
        return NOOP
    try:
        if user_id is None:
            return client.start_observation(name=name, as_type="span", input=input)
        from langfuse import propagate_attributes

        attrs = trace_attrs(user_id)
        with propagate_attributes(**attrs):
            return Handle(client.start_observation(name=name, as_type="span", input=input), attrs)
    except Exception as error:
        log("WARNING", "트레이스를 열지 못했습니다.", error=type(error).__name__)
        return NOOP


def flush():
    """모아 둔 기록을 지금 보낸다. Cloud Run 은 응답을 마친 뒤 CPU 를 거의 안 줘서 백그라운드 전송이 멈추므로,
    응답을 마칠 때 직접 보낸다(N054 7장: staging 에서 대화 5번 중 3번의 트레이스가 빠졌다). 대가는 응답 마무리가 조금 늦어지는 것이다."""
    client = get_client()
    if client is not None:
        _safe(lambda: client.flush())


def set_trace_meta(trace, **meta):
    """이 트레이스에서 앞으로 만들 관찰 단위(와 트레이스)에 metadata 를 붙인다. 속성을 같이 쓰는 Handle 에서만 동작한다.
    N055 5장: 어느 A/B 그룹이었는지(ab_variant)를 남기는 데 쓴다."""
    attrs = getattr(trace, "attrs", None)
    if attrs is not None:
        attrs["metadata"] = {**(attrs.get("metadata") or {}), **{k: str(v) for k, v in meta.items()}}


def trace_meta(trace):
    """메시지 meta 에 넣을 트레이스 ID. 반응(좋아요·별로예요·신고)을 그 답의 트레이스에 점수로 붙이려고 저장한다. 계측이 꺼져 있으면 비어 있다."""
    trace_id = getattr(trace, "trace_id", None)
    return {"trace_id": trace_id} if trace_id else {}


def score(trace_id, name, value, comment=None):
    """트레이스에 BOOLEAN 점수를 붙이고 보낸다(1 = true, 0 = false). 키가 없거나 트레이스 ID 를 모르면 건너뛴다.
    점수를 붙였으면 True. 코멘트(신고 사유 등 사용자가 쓴 글)는 보내기 전에 가린다."""
    client = get_client()
    if client is None or not trace_id:
        return False
    # 점수에도 환경을 붙여야 Scores 화면에서 local·staging·production 을 나눠 본다(안 붙이면 default 로 들어간다)
    fields = {"name": name, "value": float(value), "trace_id": trace_id, "data_type": "BOOLEAN",
              "environment": os.environ.get("ENV") or "local"}
    if comment:
        fields["comment"] = mask(data=str(comment))[:500]
    _safe(lambda: client.create_score(**fields))
    flush()
    return True


def _safe(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except Exception as error:
        log("WARNING", "관측 기록 실패", error=type(error).__name__)
        return None


def child(parent, name, as_type="span", **fields):
    """parent 아래에 관찰 단위 하나를 연다."""
    return _safe(parent.start_observation, name=name, as_type=as_type, **fields) or NOOP


def update(obs, **fields):
    """관찰 단위에 값을 적는다(닫지는 않는다)."""
    _safe(obs.update, **fields)


def close_trace(trace, **fields):
    """트레이스를 닫고 기록을 보낸다. 한 번만 한다.

    반드시 응답의 마지막 이벤트(done)를 **보내기 전에** 불러야 한다. 마지막 이벤트를 보내면 브라우저가 연결을 닫고 Cloud Run 은
    그 요청을 끝난 것으로 보고 CPU 를 거의 안 주는데, 마지막 이벤트 뒤의 코드(제너레이터의 finally)는 그때 돌아서 전송이 멈춘다
    (N054 7장: 마지막 이벤트 뒤에 flush 를 두었더니 5번 중 2번이 여전히 빠졌다. memory-update 를 done 앞에 두는 것과 같은 이유)."""
    if getattr(trace, "closed", False):
        return
    finish(trace, **fields)
    flush()
    try:
        trace.closed = True
    except AttributeError:
        pass


def finish(obs, **fields):
    """관찰 단위에 마지막 값을 적고 닫는다."""
    if fields:
        _safe(obs.update, **fields)
    _safe(obs.end)


# ── LLM 호출을 generation 으로 남기는 감싸개 ──────────────────────────

def _usage_details(usage):
    return {"input": usage["input"], "output": usage["output"]} if usage else None


def _call(function, *args, usage):
    """usage 인자를 받는 LLM 메서드면 넘기고, 받지 않는 것(테스트용 가짜 등)이면 빼고 부른다."""
    try:
        takes_usage = "usage" in inspect.signature(function).parameters
    except (TypeError, ValueError):
        takes_usage = False
    return function(*args, usage=usage) if takes_usage else function(*args)


class Step:
    """한 단계 안에서 일어난 LLM 호출들을 같은 이름의 generation 으로 묶는다."""

    def __init__(self, name):
        self.name = name
        self.generations = []
        self.final = {}

    def update(self, **fields):
        """단계가 끝날 때 generation 에 덧붙일 값(output, metadata 등)."""
        if "metadata" in fields and "metadata" in self.final:
            self.final["metadata"] = {**self.final["metadata"], **fields.pop("metadata")}
        self.final.update(fields)


class TracedLLM:
    """llm 과 같은 모양(complete_json, complete_text, chat_stream, can_reason, model)으로 쓰면서
    부를 때마다 모델 이름·입력·출력·토큰을 generation 으로 남긴다. router.py, memory.py 는 고치지 않아도 된다."""

    def __init__(self, llm, parent):
        self._llm = llm
        self._parent = parent
        self._step = None

    @property
    def can_reason(self):
        return getattr(self._llm, "can_reason", False)

    @property
    def model(self):
        return getattr(self._llm, "model", "unknown")

    @contextmanager
    def step(self, name, always=False):
        """with 안에서 부른 LLM 호출을 name 이라는 generation 으로 남긴다.
        always=True 면 모델을 안 불렀어도(규칙이 정했을 때) 토큰 0짜리 generation 을 하나 남겨 계측 누락과 구분한다."""
        step = Step(name)
        self._step = step
        try:
            yield step
        finally:
            self._step = None
            if always and not step.generations:
                empty = self._open(name, None, step)
                _safe(empty.update, usage_details={"input": 0, "output": 0})  # 모델을 안 불렀으니 토큰 0으로 분명히 남긴다
                step.generations.append(empty)
            if step.generations:
                last = step.generations[-1]
                if step.final:
                    _safe(last.update, **step.final)
            for generation in step.generations:
                _safe(generation.end)

    def _open(self, name, input, step):
        return child(self._parent, name, "generation", model=self.model, input=input)

    def _begin(self, input):
        step = self._step
        generation = self._open(step.name if step else "llm-call", input, step)
        if step:
            step.generations.append(generation)
        return generation, step

    @staticmethod
    def _fail(generation, error):
        _safe(generation.update, level="ERROR", status_message=type(error).__name__)

    def complete_json(self, system, user):
        generation, step = self._begin([{"role": "system", "content": system}, {"role": "user", "content": user}])
        usage = {}
        try:
            output = _call(self._llm.complete_json, system, user, usage=usage)
        except Exception as error:
            self._fail(generation, error)
            if not step:
                _safe(generation.end)
            raise
        _safe(generation.update, output=output, usage_details=_usage_details(usage))
        if not step:
            _safe(generation.end)
        return output

    def complete_text(self, messages):
        generation, step = self._begin(messages)
        usage = {}
        try:
            output = _call(self._llm.complete_text, messages, usage=usage)
        except Exception as error:
            self._fail(generation, error)
            if not step:
                _safe(generation.end)
            raise
        _safe(generation.update, output=output, usage_details=_usage_details(usage))
        if not step:
            _safe(generation.end)
        return output

    def chat_stream(self, messages):
        """글이 나오는 대로 그대로 내보내면서, 첫 글자가 나온 시각과 전체 답을 남긴다."""
        generation, step = self._begin(messages)
        usage = {}
        parts = []
        first = False
        try:
            for chunk in _call(self._llm.chat_stream, messages, usage=usage):
                if not first:
                    first = True
                    _safe(generation.update, completion_start_time=datetime.now(timezone.utc))
                parts.append(chunk)
                yield chunk
        except GeneratorExit:
            raise
        except Exception as error:
            self._fail(generation, error)
            raise
        finally:
            _safe(generation.update, output="".join(parts), usage_details=_usage_details(usage))
            if not step:
                _safe(generation.end)
