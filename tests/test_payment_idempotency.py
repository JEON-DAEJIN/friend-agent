"""같은 결제가 두 번 와도 크레딧이 한 번만 들어가는가(N056 7장). 실제 토스페이먼츠는 부르지 않는다.

가짜 승인 응답의 모양은 토스 결제 승인 응답의 일부 필드(paymentKey·orderId·status·totalAmount·approvedAt)를 따르고,
'처리 중' 오류는 토스 멱등키 문서의 409 IDEMPOTENT_REQUEST_PROCESSING 을 흉내 낸다.

교안의 네 가지: 같은 승인 요청 두 번 · 금액을 바꾼 요청 · 다른 사용자의 주문 · 승인 실패.
더한 것: 거의 동시에 두 번(둘 다 '대기'를 읽음) · 처리 중(409) 뒤 같은 키로 다시 · 완료 뒤 빠진 크레딧 이어 쌓기 · 응답 없음(네트워크).
"""
import itertools
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main, payments
from app.auth import User
from app.main import app, current_user, store

client = TestClient(app)
_ids = itertools.count(1)


def as_user(user_id):
    app.dependency_overrides[current_user] = lambda: User(user_id, anonymous=True)


@pytest.fixture
def uid(monkeypatch):
    monkeypatch.setattr(main.settings, "toss_client_key", "test_gck_fake_for_tests_only")
    monkeypatch.setattr(main.settings, "toss_secret_key", "test_gsk_fake_for_tests_only")
    monkeypatch.setattr(main.time, "sleep", lambda s: None)   # 409 다시 묻기 사이의 기다림을 건너뛴다
    user_id = f"idem-test-{next(_ids)}"
    as_user(user_id)
    client.get("/api/credits")   # 가입 무료 크레딧을 먼저 쌓아 둔다
    yield user_id
    app.dependency_overrides.pop(current_user, None)


class FakeToss:
    """가짜 승인 API. 실제 토스처럼 같은 멱등키로 다시 오면 다시 처리하지 않고 첫 응답을 돌려준다."""

    def __init__(self, fail=None, processing_times=0, network_error_times=0, barrier=None):
        self.calls, self.first = [], {}
        self.fail, self.processing_times, self.network_error_times, self.barrier = fail, processing_times, network_error_times, barrier
        self.approvals = 0   # 실제로 '승인이 일어난' 횟수(돈이 빠진 횟수)

    def __call__(self, secret_key, payment_key, order_id, amount, idempotency_key):
        self.calls.append({"orderId": order_id, "amount": amount, "idempotencyKey": idempotency_key})
        if self.barrier:
            self.barrier.wait(timeout=5)
        if self.network_error_times:
            self.network_error_times -= 1
            raise httpx.ConnectError("응답 전에 연결이 끊김")
        if self.processing_times:
            self.processing_times -= 1
            raise payments.TossError(409, payments.STILL_PROCESSING, "이전 멱등 요청이 처리 중입니다.")
        if self.fail:
            raise payments.TossError(400, self.fail, "카드 한도가 초과되었습니다.")
        if idempotency_key not in self.first:
            self.approvals += 1
            self.first[idempotency_key] = {"paymentKey": payment_key, "orderId": order_id, "status": "DONE",
                                           "totalAmount": amount, "approvedAt": "2026-11-04T15:00:00+09:00"}
        return self.first[idempotency_key]


def toss(monkeypatch, **kw):
    fake = FakeToss(**kw)
    monkeypatch.setattr(payments, "confirm_payment", fake)
    return fake


def new_order(product_id="credits_550"):
    return client.post("/api/orders", json={"product_id": product_id}).json()


def confirm(o, amount=None, payment_key="pk_test"):
    return client.post("/api/payments/confirm",
                       json={"paymentKey": payment_key, "orderId": o["orderId"], "amount": o["amount"] if amount is None else amount})


def purchase_rows(user_id, order_id):
    return [e for e in store.credit_history(user_id, 50) if e["ref"] == f"order:{order_id}"]


# ── 교안의 네 가지 ─────────────────────────────────────────────────

def test_same_confirm_twice_gives_credits_once(uid, monkeypatch):
    """성공 페이지 새로고침: 두 번째 요청은 주문 상태(완료)에서 멈추고 승인 API를 다시 부르지 않는다."""
    fake = toss(monkeypatch)
    o = new_order()
    before = store.credit_balance(uid)
    assert confirm(o).json()["already"] is False
    second = confirm(o).json()
    assert second["already"] is True and second["balance"] == before + 550
    assert store.credit_balance(uid) == before + 550
    assert len(purchase_rows(uid, o["orderId"])) == 1
    assert len(fake.calls) == 1 and fake.calls[0]["idempotencyKey"] == o["orderId"]


def test_tampered_amount_never_calls_toss(uid, monkeypatch):
    """주소의 amount 를 바꾼 요청: 승인 API를 한 번도 부르지 않고, 주문은 대기로 남고, 크레딧은 그대로."""
    fake = toss(monkeypatch)
    o = new_order("credits_1200")
    before = store.credit_balance(uid)
    r = confirm(o, amount=100)
    assert r.status_code == 400 and r.json()["error"] == "amount_mismatch"
    assert fake.calls == []
    assert store.get_order(uid, o["orderId"])["status"] == "pending"
    assert store.credit_balance(uid) == before


def test_other_users_order_never_calls_toss(uid, monkeypatch):
    """다른 사용자의 주문 번호로 승인 요청: 404, 승인 API를 한 번도 부르지 않는다."""
    fake = toss(monkeypatch)
    o = new_order()
    as_user(f"{uid}-someone-else")
    r = confirm(o)
    assert r.status_code == 404
    assert fake.calls == []
    as_user(uid)
    assert store.get_order(uid, o["orderId"])["status"] == "pending"


def test_rejected_approval_leaves_no_credits(uid, monkeypatch):
    """승인 실패(토스가 거절): 주문은 실패, 크레딧은 지급하지 않는다."""
    toss(monkeypatch, fail="REJECT_CARD_COMPANY")
    o = new_order()
    before = store.credit_balance(uid)
    r = confirm(o)
    assert r.status_code == 400 and r.json()["code"] == "REJECT_CARD_COMPANY"
    assert store.get_order(uid, o["orderId"])["status"] == "failed"
    assert store.credit_balance(uid) == before and purchase_rows(uid, o["orderId"]) == []


# ── 더한 것 ────────────────────────────────────────────────────────

def test_two_confirms_at_once_both_reading_pending(uid, monkeypatch):
    """교안 7장 이해 점검의 상황: 두 요청이 거의 동시에 와서 둘 다 '대기'를 읽었다.
    장치 ①(주문 상태)은 뚫리지만 ③ 같은 멱등키라 승인은 한 번, ② 원장 참조가 유일해서 크레딧도 한 번."""
    fake = toss(monkeypatch, barrier=threading.Barrier(2))
    o = new_order()
    before = store.credit_balance(uid)
    results = []
    threads = [threading.Thread(target=lambda: results.append(confirm(o).status_code)) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [200, 200]
    assert len(fake.calls) == 2                                       # ① 은 뚫렸다(둘 다 승인 API까지 감)
    assert {c["idempotencyKey"] for c in fake.calls} == {o["orderId"]}  # ③ 같은 키
    assert fake.approvals == 1                                        # 실제 승인(돈)은 한 번
    assert len(purchase_rows(uid, o["orderId"])) == 1                 # ② 크레딧 줄도 한 번
    assert store.credit_balance(uid) == before + 550


def test_still_processing_is_asked_again_with_the_same_key(uid, monkeypatch):
    fake = toss(monkeypatch, processing_times=1)
    o = new_order()
    assert confirm(o).status_code == 200
    assert [c["idempotencyKey"] for c in fake.calls] == [o["orderId"], o["orderId"]]   # 키를 바꾸지 않았다
    assert fake.approvals == 1 and len(purchase_rows(uid, o["orderId"])) == 1


def test_network_error_keeps_order_pending_and_retry_finishes_it(uid, monkeypatch):
    """승인 응답을 받지 못함: 주문을 실패로 바꾸지 않고 대기로 둔다. 다시 보내면 같은 키로 이어서 끝난다."""
    fake = toss(monkeypatch, network_error_times=1)
    o = new_order()
    before = store.credit_balance(uid)
    assert confirm(o).status_code == 502
    assert store.get_order(uid, o["orderId"])["status"] == "pending" and store.credit_balance(uid) == before
    assert confirm(o).status_code == 200
    assert store.credit_balance(uid) == before + 550
    assert {c["idempotencyKey"] for c in fake.calls} == {o["orderId"]}


def test_paid_order_without_credit_row_is_healed_once(uid, monkeypatch):
    """승인 뒤 주문을 완료로 바꿨는데 원장 기록 직전에 멈춘 상황: 새로고침하면 빠진 크레딧이 한 번만 쌓인다."""
    toss(monkeypatch)
    o = new_order()
    store.mark_order_paid(uid, o["orderId"], "pk_test", main.datetime.now(main.timezone.utc))   # 원장 없이 완료만
    before = store.credit_balance(uid)
    assert confirm(o).json()["already"] is True
    assert confirm(o).json()["already"] is True
    assert store.credit_balance(uid) == before + 550
    assert len(purchase_rows(uid, o["orderId"])) == 1
