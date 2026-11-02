"""메시지 하나를 처리하는 흐름(한 턴).

① 안전장치: 위기 표현이면 정해진 안전 응답으로 끝낸다(코드 규칙).
② 도구 선택: 모델이 도구를 0~2개 고른다. 확신이 낮으면 되묻는다(router.py).
③ 도구 실행: 날씨, 미세먼지, 공휴일, 약속(tools.py).
④ 답 생성: 캐릭터 지시문 + 기억 + 도구 결과 + 최근 대화로 답을 스트리밍한다.
⑤ 기억 정리: 답을 다 보낸 뒤 같은 요청 안에서 기억 메모와 대화 요약을 갱신한다(memory.py).
   Cloud Run 기본 설정(요청 기반 과금)에서는 응답이 끝난 뒤 CPU가 거의 주어지지 않아서,
   응답 뒤 백그라운드로 돌리면 기억 정리가 멈추거나 늦어진다. 그래서 응답을 닫기 전에 처리한다.

결과는 이벤트(dict)로 하나씩 내보낸다. show_panel 이 꺼져 있으면(production) 개발용 이벤트는 아예 만들지 않는다.
"""
from datetime import timedelta

from app import memory, observe, when
from app.logs import log
from app.persona import DEFAULT_CHARACTER, build_persona
from app.router import choose
from app.safety import CRISIS_REPLY, is_crisis
from app.tools import TOOLS, Context, run_tool

GREETING_GAP = timedelta(hours=6)  # 마지막 대화 뒤 이만큼 지나면 앱을 열 때 먼저 말을 건다


def tool_note(tool_lines):
    """방금 조회한 도구 결과를 질문 바로 앞에 한 번 더 놓는 메시지.

    시스템 지시문은 대화 맨 앞에 있어서, 앞선 대화에 내가 한 틀린 답이 여러 개 쌓이면 모델이 정확한 도구 결과보다
    그 말을 따라 한다(N054 6장: 공휴일, 같은 입력을 다시 보내면 12번 모두 오답, 질문 바로 앞에 이 메시지를 넣으면 11/12 정답).
    Claude 는 시스템 메시지를 한곳에 모아 보내므로(llm.py to_claude) 이 위치 효과는 OpenAI 호환 모델에서만 생긴다.
    """
    if not tool_lines:
        return []
    lines = "\n".join(f"- {line}" for line in tool_lines)
    return [{"role": "system", "content": f"[방금 조회한 도구 결과 — 앞선 대화에서 내가 한 말과 달라도 이 결과를 따른다]\n{lines}"}]


def system_prompt(character, now, location, memories, summary, tool_lines=(), clarify=False):
    parts = [build_persona(character),
             f"[지금] {when.label(now)} ({now.tzinfo}), 사용자 지역: {location}\n[달력]\n{when.calendar_hint(now)}"]
    block = memory.memory_block(memories, summary)
    if block:
        parts.append(block)
    if tool_lines:
        parts.append("[도구 결과]\n" + "\n".join(f"- {line}" for line in tool_lines))
    if clarify:
        parts.append("[되묻기]\n사용자가 무엇을 원하는지 확실하지 않다. 짧게 되물어서 확인한다.")
    return "\n\n".join(parts)


def run_turn(message, uid, llm, store, show_panel):
    """한 턴을 처리하면서 chat-turn 트레이스 하나에 단계별 관찰 단위를 남긴다(app/observe.py)."""
    root = observe.start_trace("chat-turn", input=message, user_id=uid)
    traced = observe.TracedLLM(llm, root)
    try:
        yield from _run_turn(message, uid, traced, store, show_panel, root)
    except Exception as error:
        observe.finish(root, level="ERROR", status_message=type(error).__name__)
        raise
    finally:
        observe.finish(root)
        observe.flush()


def _run_turn(message, uid, llm, store, show_panel, root):
    # 대화 준비 — 저장소(Supabase)에서 설정·캐릭터·기억·요약·최근 대화를 읽고 내 메시지를 저장한다.
    # 6장 실습에서 총 시간 8.4초 중 약 3.5초가 이 구간인데 단계로 안 보였다(저장소 접근은 내 컴퓨터 메모리 저장소에서는 0.01초).
    load = observe.child(root, "load-context")
    try:
        settings = store.get_settings(uid)
        character = store.get_character(uid) or DEFAULT_CHARACTER
        now = when.now_in(settings["timezone"])
        memories, summary, recent = memory.build_context(store, uid)
        history = [{"role": m["role"], "content": m["content"]} for m in recent]
        store.add_message(uid, "user", message)
        observe.update(load, metadata={"memories": len(memories), "recent_messages": len(history)})
    finally:
        observe.finish(load)

    # ① 위기 표현
    safety = observe.child(root, "safety", input=message)
    crisis = is_crisis(message)
    observe.finish(safety, output={"crisis": crisis})
    if crisis:
        root.tag("safety-response")
        guards = ["위기 표현이 있어 캐릭터 설정과 상관없이 정해진 안전 응답을 보냈습니다."]
        if show_panel:
            yield {"type": "turn", "tools": [], "confidence": None, "reason": "", "decided_by": "rule",
                   "action": "safety", "guards": guards}
        yield {"type": "delta", "text": CRISIS_REPLY}
        saved = store.add_message(uid, "assistant", CRISIS_REPLY, {"action": "safety", "guards": guards,
                                                                    **observe.trace_meta(root)})
        log("WARNING", "위기 표현 안전 응답")
        observe.update(root, output=CRISIS_REPLY, metadata={"action": "safety"})
        yield {"type": "done", "message_id": saved["id"]}
        return

    # ② 도구 선택 — 모델이 고르면 토큰이 남고, 규칙이 고르면 토큰 0이므로 decided_by 로 구분한다
    with llm.step("choose-tools", always=True) as step:
        choice = choose(message, history, now, settings["location"], llm)
        root.tag(*(t["name"] for t in choice.tools))
        step.update(output={"tools": choice.tools, "reason": choice.reason},
                    metadata={"tools": ",".join(t["name"] for t in choice.tools) or "none",
                              "confidence": round(choice.confidence, 2), "decided_by": choice.decided_by,
                              "action": choice.action})
    if show_panel:
        yield {"type": "turn", "tools": [{"name": t["name"], "label": TOOLS[t["name"]]["label"], "args": t["args"]}
                                         for t in choice.tools],
               "confidence": choice.confidence, "reason": choice.reason, "decided_by": choice.decided_by,
               "action": choice.action, "guards": choice.guards}

    # ③ 도구 실행 — 도구마다 하나씩, 성공 여부를 남긴다
    ctx = Context(store, uid, settings["location"], settings["timezone"])
    results = []
    for t in choice.tools:
        tool_obs = observe.child(root, f"tool:{t['name']}", input=t["args"])
        try:
            r = run_tool(t["name"], t["args"], ctx)
        except Exception as error:
            observe.finish(tool_obs, level="ERROR", status_message=type(error).__name__, metadata={"ok": False})
            raise
        observe.finish(tool_obs, output=r.summary, metadata={"ok": r.ok}, level=None if r.ok else "WARNING")
        results.append(r)
    for r in results:
        if show_panel:
            yield {"type": "tool", "name": r.name, "label": TOOLS[r.name]["label"], "ok": r.ok,
                   "summary": r.summary, "data": r.data}
        if r.ok and r.name in ("create_reminder", "cancel_reminder"):
            yield {"type": "reminder", "action": r.name, "summary": r.summary}

    # ④ 답 생성 — 첫 글자가 나온 시각까지 남긴다(TracedLLM 이 completion_start_time 으로 기록)
    system = system_prompt(character, now, settings["location"], memories, summary,
                           [r.summary for r in results], clarify=choice.action == "clarify")
    if show_panel:
        yield {"type": "context", "memories": [m["text"] for m in memories],
               "summary": summary["text"] if summary else None, "recent_messages": len(history)}
    reply = ""
    with llm.step("reply", always=True) as step:
        step.update(metadata={"memories": len(memories), "recent_messages": len(history),
                              "tools": ",".join(t["name"] for t in choice.tools) or "none"})
        for chunk in llm.chat_stream([{"role": "system", "content": system}, *history,
                                      *tool_note([r.summary for r in results]), {"role": "user", "content": message}]):
            reply += chunk
            yield {"type": "delta", "text": chunk}
    meta = {"tools": [t["name"] for t in choice.tools], "confidence": choice.confidence,
            "action": choice.action, "guards": choice.guards, **observe.trace_meta(root)}
    save = observe.child(root, "save-turn")  # 답 저장(저장소 쓰기)
    try:
        saved = store.add_message(uid, "assistant", reply, meta)
    finally:
        observe.finish(save)

    # ⑤ 기억 정리 — 답이 다 나간 뒤 같은 요청 안에서 돈다. 사용자는 기다리지 않지만 비용은 든다
    yield {"type": "memory_start"}
    with llm.step("memory-update", always=True) as step:
        changes = memory.update_memories(store, uid, message, reply, now, llm)
        step.update(output={"added": [m["text"] for m in changes["added"]],
                            "updated": [m["text"] for m in changes["updated"]],
                            "removed": [m["text"] for m in changes["removed"]]},
                    metadata={"added": len(changes["added"]), "updated": len(changes["updated"]),
                              "removed": len(changes["removed"])})
    with llm.step("memory-summary") as step:  # 요약할 때만 모델을 부르므로 호출이 있을 때만 남는다
        check = observe.child(root, "summary-check")  # 요약할 때가 됐는지 저장소에서 확인하는 시간
        summarized = None
        try:
            summarized = memory.maybe_summarize(store, uid, llm)
        finally:
            observe.finish(check, metadata={"summarized": summarized})
        step.update(metadata={"summarized": summarized})
    yield {"type": "memory", "added": [m["text"] for m in changes["added"]],
           "updated": [m["text"] for m in changes["updated"]], "removed": [m["text"] for m in changes["removed"]]}
    observe.update(root, output=reply, metadata={"action": choice.action,
                                                 "tools": ",".join(t["name"] for t in choice.tools) or "none"})
    yield {"type": "done", "message_id": saved["id"]}


def greet(uid, llm, store):
    """앱을 열 때 먼저 말을 건다. 때가 된 약속이 있거나, 대화가 처음이거나, 오래 쉬었을 때만.

    약속은 '전달됨'으로 바꾸는 데 성공한 것만 전한다. 창을 두 개 열어도 같은 약속을 두 번 전하지 않는다.
    """
    settings = store.get_settings(uid)
    character = store.get_character(uid) or DEFAULT_CHARACTER
    now = when.now_in(settings["timezone"])
    due = store.claim_due_reminders(uid, now)
    last = store.recent_messages(uid, 1)
    if not due and last and now - last[-1]["created_at"] < GREETING_GAP:
        return None

    memories, summary, _ = memory.build_context(store, uid)
    lines = [f"약속 알림: '{r['text']}' ({when.label(r['due_at'].astimezone(now.tzinfo))} 예정이었음)" for r in due]
    system = system_prompt(character, now, settings["location"], memories, summary, lines)
    guide = ["사용자가 방금 앱을 열었다. 네가 먼저 1~2문장으로 말을 건다."]
    guide.append("[도구 결과]의 약속 알림을 꼭 전한다." if due else "전할 약속은 없다. 약속이나 알림 이야기를 꺼내지 않는다.")
    guide.append("기억 메모에 오늘이거나 이미 지난 일정이 있으면 어땠는지 물어본다.")
    if not last:
        guide.append("처음 만난 사이다. 이름을 넣어 짧게 자기소개를 한다.")
    system += "\n\n[먼저 말 걸기]\n" + " ".join(guide)
    # 말을 걸 때만 트레이스를 만든다(대화가 이어지는 중에 앱을 열 때마다 빈 트레이스가 쌓이지 않게)
    root = observe.start_trace("greeting", input={"reminders": len(due), "first_time": not last}, user_id=uid)
    traced = observe.TracedLLM(llm, root)
    try:
        with traced.step("greeting-reply", always=True):
            text = traced.complete_text([{"role": "system", "content": system},
                                         {"role": "user", "content": "(앱을 열었다. 먼저 말을 걸어 줘)"}])
        observe.finish(root, output=text)
    except Exception as error:
        observe.finish(root, level="ERROR", status_message=type(error).__name__)
        raise
    finally:
        observe.flush()
    saved = store.add_message(uid, "assistant", text, {"action": "greeting", "reminders": [r["id"] for r in due],
                                                       **observe.trace_meta(root)})
    log("INFO", "먼저 말 걸기", reminders=len(due))
    return {"id": saved["id"], "text": text, "reminders": [r["text"] for r in due]}
