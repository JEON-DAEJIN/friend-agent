"""크레딧 결제(N056 6장). 실제 토스페이먼츠는 부르지 않고 승인 함수를 가짜로 바꿔 끼운다.

확인 목록(학습서 6장)의 서버 쪽 항목: 상품표는 서버 한 곳에서 내려온다 · 주문은 상품 ID만 받고 금액은 서버가 정한다 ·
시크릿 키는 /api/config 에 없다 · 크레딧을 늘리는 곳은 승인 처리 한 곳이고 승인 성공 뒤에만 늘린다.
"""
import itertools

import pytest
from fastapi.testclient import TestClient

from app import main, payments
from app.auth import User
from app.main import app, current_user, store

client = TestClient(app)
_ids = itertools.count(1)
SECRET = "test_gsk_fake_for_tests_only"


@pytest.fixture
def uid(monkeypatch):
    monkeypatch.setattr(main.settings, "toss_client_key", "test_gck_fake_for_tests_only")
    monkeypatch.setattr(main.settings, "toss_secret_key", SECRET)
    user_id = f"pay-test-{next(_ids)}"
    app.dependency_overrides[current_user] = lambda: User(user_id, anonymous=True)
    yield user_id
    app.dependency_overrides.pop(current_user, None)


@pytest.fixture
def toss(monkeypatch):
    """승인 API 호출을 가짜로. 토스 결제 승인 응답의 일부 필드(paymentKey·orderId·status·totalAmount·approvedAt) 모양을 따른다."""
    calls = []

    def fake_confirm(secret_key, payment_key, order_id, amount):
        calls.append({"secret": secret_key, "paymentKey": payment_key, "orderId": order_id, "amount": amount})
        return {"paymentKey": payment_key, "orderId": order_id, "status": "DONE", "totalAmount": amount,
                "approvedAt": "2026-11-04T15:00:00+09:00"}

    monkeypatch.setattr(payments, "confirm_payment", fake_confirm)
    return calls


def order(product_id="credits_550", **extra):
    return client.post("/api/orders", json={"product_id": product_id, **extra}).json()


def test_products_come_from_the_server_table(uid):
    body = client.get("/api/products").json()
    assert body["payments_enabled"] is True
    assert [(p["id"], p["amount"], p["credits"]) for p in body["products"]] == [
        ("credits_100", 1000, 100), ("credits_550", 5000, 550), ("credits_1200", 10000, 1200)]


def test_order_ignores_amount_sent_by_the_screen(uid):
    o = order("credits_100", amount=100, credits=1200)   # 화면이 금액·크레딧을 보내도 쓰지 않는다
    assert (o["amount"], o["credits"]) == (1000, 100)
    saved = store.get_order(uid, o["orderId"])
    assert saved["status"] == "pending" and saved["amount"] == 1000


def test_unknown_product_is_rejected(uid):
    assert client.post("/api/orders", json={"product_id": "credits_999999"}).status_code == 400


def test_config_has_client_key_but_never_secret_key(uid):
    text = client.get("/api/config").text
    assert "test_gck_fake_for_tests_only" in text
    assert SECRET not in text and "test_gsk" not in text


def test_confirm_adds_credits_only_after_approval(uid, toss):
    before = client.get("/api/credits").json()["balance"]
    o = order()
    assert store.credit_balance(uid) == before          # 주문만으로는 늘지 않는다
    r = client.post("/api/payments/confirm", json={"paymentKey": "pk_test_1", "orderId": o["orderId"], "amount": 5000})
    assert r.status_code == 200 and r.json()["credits"] == 550
    assert store.credit_balance(uid) == before + 550
    assert toss == [{"secret": SECRET, "paymentKey": "pk_test_1", "orderId": o["orderId"], "amount": 5000}]
    saved = store.get_order(uid, o["orderId"])
    assert saved["status"] == "paid" and saved["payment_key"] == "pk_test_1" and saved["approved_at"]
    assert store.credit_history(uid, 1)[0]["ref"] == f"order:{o['orderId']}"


def test_fail_page_marks_order_failed_without_calling_toss(uid, toss):
    o = order()
    r = client.post("/api/payments/fail", json={"orderId": o["orderId"], "code": "PAY_PROCESS_CANCELED", "message": "취소"})
    assert r.status_code == 200
    assert store.get_order(uid, o["orderId"])["status"] == "failed"
    assert toss == []


def test_auth_header_has_the_trailing_colon():
    # base64("test_gsk_x:") — 콜론이 빠지면 토스가 인증을 거절한다(LLM Quick Reference '자주 틀리는 패턴')
    assert payments.auth_header("test_gsk_x") == "Basic dGVzdF9nc2tfeDo="


def test_access_log_hides_payment_key():
    """성공 주소가 웹 서버 접속 로그에 찍혀도 paymentKey 는 앞 4자만 남는다(N056 실습에서 전체가 찍힌 것을 발견)."""
    import logging

    from app.logs import MaskPaymentKey
    path = "/payments/success?paymentType=NORMAL&orderId=ord_abc&paymentKey=tgen_ABCDEFGHIJ&amount=1000"
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                               ("127.0.0.1:1", "GET", path, "1.1", 200), None)
    MaskPaymentKey().filter(record)
    line = record.getMessage()
    assert "paymentKey=tgen***" in line and "ABCDEFGHIJ" not in line
    assert "orderId=ord_abc" in line and "amount=1000" in line
