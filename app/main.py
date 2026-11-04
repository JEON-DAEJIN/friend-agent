"""AI 친구 에이전트 서비스.

화면(/)과 API를 FastAPI 하나가 함께 제공한다.
- /health, /version: 서비스 상태와 배포 버전
- /api/config: 화면이 쓰는 설정(로그인 방식, 개발용 패널 표시 여부)
- /api/character, /api/settings: 캐릭터와 사용자 설정
- /api/chat: 메시지 한 턴. 결과를 이벤트(JSON 한 줄씩)로 스트리밍한다. 보내기 전에 크레딧 1개를 서버에서 뺀다
- /api/credits: 남은 크레딧과 최근 원장 기록
- /api/products, /api/orders, /api/payments/confirm, /api/payments/fail: 크레딧 결제(토스페이먼츠 결제위젯 v2)
- /payments/success, /payments/fail: 결제 뒤 토스가 돌려보내는 주소. 화면(/)과 같은 페이지를 보여 준다
- /api/greeting: 앱을 열 때 먼저 말 걸기
- /api/ocr: 사진 속 글자 읽기(OCR_ENABLED=true 일 때)
- /api/memories, /api/reminders, /api/data: 기억, 약속, 내 데이터 관리
"""
import html
import json
import os
import time
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from app import agent, observe, ocr, payments, when
from app.auth import Auth, User
from app.config import ROOT, load_settings
from app.llm import make_llm
from app.logs import install_access_log_mask, log
from app.persona import DEFAULT_CHARACTER, OPTIONS, build_persona, clean_character
from app.safety import is_crisis
from app.store.base import make_store
from app.tools import CITIES

settings = load_settings()  # 꼭 필요한 값이 없으면 여기서 종료한다
install_access_log_mask()  # 접속 로그 주소의 paymentKey 를 가린다(app/logs.py)
llm = make_llm(settings)
store = make_store(settings)
current_user = Auth(settings)

app = FastAPI(title="AI 친구 에이전트")

# 시작 지연 스위치(배포 실습용). 포트는 바로 열지만 정한 시간 동안은 준비 중이라 모든 요청에 503을 돌려준다.
# AI 서비스가 시작할 때 모델 설정이나 색인을 불러오느라 늦게 준비되는 상황을 흉내 낸다. 기본값 0(끔).
SLOW_START_SECONDS = float(os.environ.get("SLOW_START_SECONDS", "0") or 0)
READY_AT = time.time() + SLOW_START_SECONDS
if SLOW_START_SECONDS:
    log("WARNING", f"시작 지연 스위치가 켜져 있습니다. {SLOW_START_SECONDS:.0f}초 동안 준비 중으로 응답합니다.")


@app.middleware("http")
async def not_ready_yet(request, call_next):
    if time.time() < READY_AT:
        return JSONResponse({"status": "starting"}, status_code=503)
    return await call_next(request)


def get_llm():
    """테스트에서는 이 함수를 가짜 LLM을 돌려주는 함수로 바꿔 끼운다."""
    return llm


class ChatRequest(BaseModel):
    message: str


# 크레딧(N056). 차감 단위는 메시지 1개 = 크레딧 1개. 차감과 거절은 서버에서만 한다(화면은 보여 주기만).
CREDITS_PER_MESSAGE = 1
NO_CREDITS_MESSAGE = "크레딧을 다 썼어요. 충전하면 바로 이어서 이야기할 수 있어요."


def grant_signup_credits(uid):
    """처음 온 사용자에게 무료 크레딧을 한 번 쌓는다. 참조 signup:사용자ID 가 유일해서 몇 번 불러도 한 번만 쌓인다."""
    if settings.free_credits > 0 and store.add_credit(uid, settings.free_credits, "signup", f"signup:{uid}"):
        log("INFO", "가입 무료 크레딧 지급", amount=settings.free_credits)


class SettingsRequest(BaseModel):
    location: str | None = None
    timezone: str | None = None


class ReportRequest(BaseModel):
    message_id: int
    reason: str = ""


class FeedbackRequest(BaseModel):
    message_id: int
    value: str  # like, dislike


@app.get("/", response_class=HTMLResponse)
def home():
    page = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    banner = "" if settings.env in ("production", "local") else f'<div class="banner">테스트 환경 ({settings.env})</div>'
    if os.environ.get("BANNER"):  # 이미지를 새로 만들지 않고 환경변수만 바꿔 새 리비전을 만들 때 쓰는 화면 문구
        banner += f'<div class="banner">{html.escape(os.environ["BANNER"][:80])}</div>'
    return page.replace("<!--ENV_BANNER-->", banner)


@app.get("/payments/success", response_class=HTMLResponse)
@app.get("/payments/fail", response_class=HTMLResponse)
def payment_return():
    """결제 뒤 토스가 돌려보내는 성공·실패 주소. 같은 화면을 보여 주고, 화면이 주소의 값을 서버로 보낸다.
    성공 페이지는 크레딧을 직접 늘리지 않는다 — 늘리는 곳은 /api/payments/confirm 한 곳뿐이다."""
    return home()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/version")
def version():
    return {"commit": settings.commit, "revision": settings.revision, "env": settings.env,
            "llm": settings.provider, "model": settings.model, "storage": settings.storage, "auth": settings.auth,
            "banner": os.environ.get("BANNER", "")}


@app.get("/api/config")
def config():
    # publishable key 는 브라우저에 공개해도 되는 키다. secret key 는 절대 여기에 넣지 않는다.
    return {"env": settings.env, "auth": settings.auth, "storage": settings.storage,
            "supabase_url": settings.supabase_url if settings.auth == "supabase" else "",
            "supabase_publishable_key": settings.supabase_publishable_key if settings.auth == "supabase" else "",
            "google_login": settings.google_login, "show_turn_panel": settings.show_turn_panel,
            "ocr_enabled": settings.ocr_enabled,
            # 결제위젯 클라이언트 키는 화면에 넘겨도 된다. 시크릿 키(TOSS_SECRET_KEY)는 절대 여기에 넣지 않는다.
            "payments_enabled": bool(settings.toss_client_key and settings.toss_secret_key),
            "toss_client_key": settings.toss_client_key,
            "options": OPTIONS, "cities": list(CITIES)}


@app.get("/api/me")
def me(user: User = Depends(current_user)):
    grant_signup_credits(user.id)
    return {"id": user.id, "anonymous": user.anonymous, "email": user.email,
            "has_character": store.get_character(user.id) is not None,
            "credits": store.credit_balance(user.id)}


@app.get("/api/credits")
def credits(user: User = Depends(current_user)):
    """남은 크레딧(원장 합계)과 최근 기록 10줄. 화면은 이 값을 그대로 보여 주고 따로 계산하지 않는다."""
    grant_signup_credits(user.id)
    history = [{"delta": e["delta"], "reason": e["reason"], "created_at": e["created_at"].isoformat()}
               for e in store.credit_history(user.id, 10)]
    return {"balance": store.credit_balance(user.id), "history": history,
            "per_message": CREDITS_PER_MESSAGE, "free_on_signup": settings.free_credits}


# ── 캐릭터와 설정 ──────────────────────────────────────────────────

@app.get("/api/character")
def get_character(user: User = Depends(current_user)):
    c = store.get_character(user.id)
    return {"character": c or DEFAULT_CHARACTER, "saved": c is not None}


@app.put("/api/character")
def put_character(data: dict, user: User = Depends(current_user)):
    c = clean_character(data)
    store.save_character(user.id, c)
    log("INFO", "캐릭터 저장")
    return {"character": c}


@app.post("/api/character/preview")
def preview_character(data: dict):
    return {"prompt": build_persona(clean_character(data))}


@app.get("/api/settings")
def get_settings(user: User = Depends(current_user)):
    return store.get_settings(user.id)


@app.put("/api/settings")
def put_settings(req: SettingsRequest, user: User = Depends(current_user)):
    changes = {}
    if req.location in CITIES:
        changes["location"] = req.location
    if req.timezone:
        changes["timezone"] = req.timezone if req.timezone == "Asia/Seoul" else "Asia/Seoul"  # 지금은 한국 시간만
    store.save_settings(user.id, changes)
    return store.get_settings(user.id)


# ── 대화 ───────────────────────────────────────────────────────────

@app.get("/api/messages")
def messages(user: User = Depends(current_user)):
    show = settings.show_turn_panel
    return [{"id": m["id"], "role": m["role"], "content": m["content"],
             "meta": m["meta"] if show else {}, "created_at": m["created_at"].isoformat()}
            for m in store.recent_messages(user.id, 50)]


@app.post("/api/chat")
def chat(req: ChatRequest, user: User = Depends(current_user), model=Depends(get_llm)):
    message = req.message.strip()[:1000]
    if not message:
        raise HTTPException(400, "메시지가 비어 있습니다")

    # 답을 흘려 보내기 시작하면 상태 코드를 바꿀 수 없으므로, 보내기 전에 잔액 확인과 차감을 한 번에 한다.
    # 위기 표현에는 크레딧과 상관없이 정해진 안전 응답이 나가야 하므로 받지도 거절하지도 않는다.
    ref = None
    if not is_crisis(message):
        ref = f"chat:{uuid.uuid4().hex}"
        ok, balance = store.spend_credit(user.id, CREDITS_PER_MESSAGE, "chat", ref, settings.free_credits)
        if not ok:
            log("INFO", "크레딧 부족으로 거절", balance=balance)
            return JSONResponse({"error": "insufficient_credits", "message": NO_CREDITS_MESSAGE, "balance": balance},
                                status_code=402)

    def events():
        failed = False
        try:
            for event in agent.run_turn(message, user.id, model, store, settings.show_turn_panel):
                failed = failed or event.get("type") == "error"
                yield json.dumps(event, ensure_ascii=False, default=str) + "\n"
        except Exception as error:
            failed = True
            log("ERROR", "대화 처리 실패", error=type(error).__name__)
            yield json.dumps({"type": "error", "message": "답을 만들지 못했어요. 잠시 뒤 다시 보내 주세요."}, ensure_ascii=False) + "\n"
        if failed and ref:
            # 답을 만들지 못했으면 뺀 크레딧을 되돌리는 줄로 돌려준다(원장은 지우지 않는다)
            store.add_credit(user.id, CREDITS_PER_MESSAGE, "refund", f"refund:{ref}")
            log("INFO", "답 실패로 크레딧 되돌림", amount=CREDITS_PER_MESSAGE)

    return StreamingResponse(events(), media_type="application/x-ndjson")


@app.post("/api/greeting")
def greeting(user: User = Depends(current_user), model=Depends(get_llm)):
    return {"greeting": agent.greet(user.id, model, store)}


@app.post("/api/ocr")
async def read_photo(photo: UploadFile, user: User = Depends(current_user)):
    if not settings.ocr_enabled:
        raise HTTPException(404, "사진 글자 읽기가 꺼져 있습니다")
    try:
        return {"text": ocr.read_text(await photo.read())}
    except ocr.OcrError as error:
        raise HTTPException(500, str(error)) from error


def own_answer(user, message_id):
    """로그인한 사용자 자신의 캐릭터 답이 아니면 404. 남의 메시지에 점수를 매기면 평가 데이터가 오염된다."""
    message = store.get_message(user.id, message_id)
    if not message or message["role"] != "assistant":
        raise HTTPException(404, "메시지를 찾을 수 없습니다")
    return message


@app.post("/api/report")
def report(req: ReportRequest, user: User = Depends(current_user)):
    message = own_answer(user, req.message_id)
    log("WARNING", "부적절한 답변 신고", message_id=req.message_id, reason_length=len(req.reason))
    scored = observe.score(message["meta"].get("trace_id"), "report", 1, req.reason.strip()[:300] or None)
    return {"ok": True, "scored": scored}


@app.post("/api/feedback")
def feedback(req: FeedbackRequest, user: User = Depends(current_user)):
    """답에 대한 좋아요(user-feedback = true)와 별로예요(= false)를 그 답의 트레이스에 점수로 붙인다."""
    if req.value not in ("like", "dislike"):
        raise HTTPException(400, "value 는 like 또는 dislike 여야 합니다")
    message = own_answer(user, req.message_id)
    scored = observe.score(message["meta"].get("trace_id"), "user-feedback", 1 if req.value == "like" else 0)
    return {"ok": True, "scored": scored}


# ── 크레딧 결제(N056) ─────────────────────────────────────────────────
# 결제 흐름 1~9(학습서 5장). 크레딧을 늘리는 곳은 confirm_payment 한 곳뿐이고, 승인이 성공한 뒤에만 늘린다.

class OrderRequest(BaseModel):
    product_id: str            # 화면은 상품 ID만 보낸다. 금액·크레딧 수는 서버의 상품표가 정한다


class ConfirmRequest(BaseModel):  # successUrl 의 쿼리 그대로(이름도 토스와 같게)
    paymentKey: str
    orderId: str
    amount: int


class FailRequest(BaseModel):     # failUrl 의 쿼리 그대로
    orderId: str
    code: str = ""
    message: str = ""


def payments_enabled():
    return bool(settings.toss_client_key and settings.toss_secret_key)


@app.get("/api/products")
def products():
    return {"payments_enabled": payments_enabled(), "per_message": CREDITS_PER_MESSAGE,
            "products": [{**p, "unit_price": round(p["amount"] / p["credits"], 1)} for p in payments.PRODUCTS]}


@app.post("/api/orders")
def create_order(req: OrderRequest, user: User = Depends(current_user)):
    """결제를 시작하기 전에 주문을 대기 상태로 저장한다(시퀀스 2번). 승인할 때 비교할 기준 금액이 된다."""
    if not payments_enabled():
        raise HTTPException(503, "결제가 아직 준비되지 않았습니다")
    product = payments.PRODUCT_BY_ID.get(req.product_id)
    if not product:
        raise HTTPException(400, "알 수 없는 상품입니다")
    order_id = payments.new_order_id()
    store.create_order(user.id, order_id, product["id"], product["amount"], product["credits"])
    log("INFO", "주문 저장", product_id=product["id"], amount=product["amount"])
    return {"orderId": order_id, "orderName": product["name"], "amount": product["amount"], "credits": product["credits"]}


@app.post("/api/payments/confirm")
def confirm_payment(req: ConfirmRequest, user: User = Depends(current_user)):
    """성공 페이지가 보낸 값으로 결제를 승인하고, 성공하면 주문 완료 + 원장에 +크레딧(시퀀스 6~9번)."""
    order = store.get_order(user.id, req.orderId)
    if not order:   # 다른 사용자의 주문도 여기서 걸린다(조회에 user_id 조건)
        raise HTTPException(404, "주문을 찾을 수 없습니다")
    if order["status"] == "paid":
        # 장치 ① 주문 상태: 이미 완료된 주문이면 승인 API를 다시 부르지 않는다(새로고침·두 번 클릭).
        # 승인 뒤 주문은 완료로 바꿨는데 원장 기록 직전에 멈췄다면 여기서 이어서 쌓는다 — 참조가 유일해서 이미 있으면 쌓이지 않는다
        if store.add_credit(user.id, order["credits"], "purchase", f"order:{req.orderId}"):
            log("WARNING", "완료 주문의 빠진 크레딧을 이어서 지급", credits=order["credits"])
        return {"ok": True, "already": True, "credits": order["credits"], "balance": store.credit_balance(user.id)}
    if order["status"] != "pending":
        raise HTTPException(409, "이미 끝난 주문입니다")
    if req.amount != order["amount"]:
        # 주소의 amount 는 사용자가 바꿀 수 있다. 저장한 금액과 다르면 승인 API를 부르지 않는다(시퀀스 6번)
        log("WARNING", "결제 금액 불일치로 승인 중단", order_amount=order["amount"], sent_amount=req.amount)
        return JSONResponse({"error": "amount_mismatch", "message": "주문 금액과 결제 금액이 달라 결제를 진행하지 않았어요."},
                            status_code=400)
    # 장치 ③ Idempotency-Key: 같은 주문이면 몇 번을 다시 보내도 같은 값(주문 번호 그대로, 사용자 결정).
    # 요청마다 새로 만들면 다시 보낸 요청도 새 요청이 되어 아무것도 막지 못한다. 오류가 나도 키를 바꾸지 않는다
    idempotency_key = req.orderId
    try:
        for attempt in range(1, 4):
            log("INFO", "결제 승인 요청", order_id=req.orderId, idempotency_key=idempotency_key, attempt=attempt)
            try:
                result = payments.confirm_payment(settings.toss_secret_key, req.paymentKey, req.orderId, order["amount"],
                                                  idempotency_key)
                break
            except payments.TossError as error:
                # 첫 요청이 아직 처리 중(409): 잠시 뒤 같은 키로 다시 물어 결과를 받는다
                if error.code != payments.STILL_PROCESSING or attempt == 3:
                    raise
                time.sleep(1)
    except payments.TossError as error:
        if error.code == payments.STILL_PROCESSING:
            log("WARNING", "결제 승인이 아직 처리 중", order_id=req.orderId)
            return JSONResponse({"error": "payment_processing", "message": "결제를 처리하고 있어요. 잠시 뒤 이 페이지를 새로고침해 주세요."},
                                status_code=409)
        store.mark_order_failed(user.id, req.orderId, error.code)
        log("WARNING", "결제 승인 거절", code=error.code, status=error.status)
        return JSONResponse({"error": "payment_rejected", "code": error.code, "message": error.message or "결제가 승인되지 않았어요."},
                            status_code=400)
    except httpx.HTTPError as error:
        # 승인이 됐는지 모르는 상태다. 주문을 실패로 바꾸지 않고 대기로 둔다(다시 시도하면 이어서 처리)
        log("ERROR", "결제 승인 응답을 받지 못함", error=type(error).__name__)
        return JSONResponse({"error": "payment_unknown", "message": "결제 결과를 확인하지 못했어요. 잠시 뒤 이 페이지를 새로고침해 주세요."},
                            status_code=502)
    approved_at = datetime.fromisoformat(result["approvedAt"]) if result.get("approvedAt") else datetime.now(timezone.utc)
    store.mark_order_paid(user.id, req.orderId, req.paymentKey, approved_at)
    store.add_credit(user.id, order["credits"], "purchase", f"order:{req.orderId}")
    log("INFO", "결제 승인·크레딧 지급", amount=order["amount"], credits=order["credits"])   # paymentKey·시크릿 키는 남기지 않는다
    return {"ok": True, "already": False, "credits": order["credits"], "balance": store.credit_balance(user.id)}


@app.post("/api/payments/fail")
def payment_failed(req: FailRequest, user: User = Depends(current_user)):
    """실패 페이지: 결제를 취소했거나 인증에 실패했다. 승인 API는 부르지 않고 주문만 실패로 바꾼다."""
    changed = store.mark_order_failed(user.id, req.orderId, req.code[:60] or "UNKNOWN")
    log("INFO", "결제 실패·취소", code=req.code[:60], changed=changed)
    return {"ok": True}


# ── 기억, 약속, 내 데이터 ───────────────────────────────────────────

@app.get("/api/memories")
def list_memories(user: User = Depends(current_user)):
    return [{"id": m["id"], "kind": m["kind"], "text": m["text"]} for m in store.list_memories(user.id)]


@app.delete("/api/memories/{memory_id}")
def delete_memory(memory_id: int, user: User = Depends(current_user)):
    store.delete_memory(user.id, memory_id)
    return {"ok": True}


@app.delete("/api/memories")
def clear_memories(user: User = Depends(current_user)):
    store.clear_memories(user.id)
    return {"ok": True}


@app.get("/api/reminders")
def list_reminders(user: User = Depends(current_user)):
    tz = when.now_in(store.get_settings(user.id)["timezone"]).tzinfo
    return [{"id": r["id"], "text": r["text"], "status": r["status"],
             "due_label": when.label(r["due_at"].astimezone(tz))} for r in store.list_reminders(user.id)]


@app.delete("/api/reminders/{reminder_id}")
def cancel_reminder(reminder_id: int, user: User = Depends(current_user)):
    store.cancel_reminder(user.id, reminder_id)
    return {"ok": True}


@app.delete("/api/data")
def delete_data(user: User = Depends(current_user)):
    store.delete_all(user.id)
    log("INFO", "사용자 데이터 전체 삭제")
    return {"ok": True}
