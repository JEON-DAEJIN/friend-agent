"""크레딧 결제(N056). 토스페이먼츠 결제위젯 v2 + 서버 승인.

흐름(학습서 5장 시퀀스): 상품 고르기 → 주문을 먼저 서버에 저장(대기) → 결제위젯으로 결제 요청·카드사 인증
→ successUrl 로 돌아온 paymentKey·orderId·amount 를 서버로 → 저장한 금액·상태와 비교 → 시크릿 키로 승인
→ 성공하면 주문 완료 + 원장에 +크레딧(참조 order:주문번호).

- 상품표는 이 파일 한 곳에만 둔다. 화면은 상품 ID만 보내고, 금액과 크레딧 수는 서버가 여기서 찾는다.
- 시크릿 키는 TOSS_SECRET_KEY 에서만 읽고 화면·로그·/api/config 에 넣지 않는다.
- 승인 API 주소·인증 방식의 근거: 토스페이먼츠 LLM Quick Reference(docs.tosspayments.com/guides/v2/get-started/llms-quick-reference),
  공식 샘플 github.com/tosspayments/tosspayments-sample(php-javascript). 인증은 Basic base64("시크릿키:") — 끝의 콜론이 필수다.
"""
import base64
import uuid

import httpx

# 학습서 3장 상품표 그대로(N056 실습 진행기록 4절의 결정). 크레딧 1개 가격: 10원·약 9.1원·약 8.3원
PRODUCTS = [
    {"id": "credits_100", "name": "크레딧 100개", "amount": 1000, "credits": 100},
    {"id": "credits_550", "name": "크레딧 550개", "amount": 5000, "credits": 550},
    {"id": "credits_1200", "name": "크레딧 1,200개", "amount": 10000, "credits": 1200},
]
PRODUCT_BY_ID = {p["id"]: p for p in PRODUCTS}

CONFIRM_URL = "https://api.tosspayments.com/v1/payments/confirm"


def new_order_id():
    """추측할 수 없는 무작위 주문 번호. 토스 규칙: 영문·숫자·-_= 6~64자."""
    return f"ord_{uuid.uuid4().hex}"


def auth_header(secret_key):
    return "Basic " + base64.b64encode(f"{secret_key}:".encode()).decode()


class TossError(Exception):
    """토스가 승인을 거절했다(카드 한도, 잘못된 키 등). code·message 는 토스가 준 그대로."""

    def __init__(self, status, code, message):
        super().__init__(f"{status} {code}")
        self.status, self.code, self.message = status, code, message


# 같은 키로 다시 보냈는데 첫 요청이 아직 처리 중일 때 토스가 409와 함께 돌려주는 코드(학습서 7장)
STILL_PROCESSING = "IDEMPOTENT_REQUEST_PROCESSING"


def confirm_payment(secret_key, payment_key, order_id, amount, idempotency_key):
    """토스 결제 승인 API를 부른다. 성공하면 토스의 결제 객체(dict), 거절이면 TossError.
    네트워크 오류(httpx.HTTPError)는 그대로 올린다 — 승인이 됐는지 모르므로 주문을 실패로 바꾸지 않는다.
    idempotency_key: 같은 주문의 승인 요청에는 몇 번을 다시 보내도 같은 값이 가야 한다(N056 결정: 주문 번호 그대로).
    토스는 같은 키·같은 API 키·같은 주소로 온 두 번째 요청을 다시 처리하지 않고 첫 응답을 돌려준다(300자 이하, 15일 유효).
    테스트에서는 이 함수를 가짜로 바꿔 끼운다."""
    r = httpx.post(CONFIRM_URL, timeout=15,
                   headers={"Authorization": auth_header(secret_key), "Content-Type": "application/json",
                            "Idempotency-Key": idempotency_key},
                   json={"paymentKey": payment_key, "orderId": order_id, "amount": amount})
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200:
        raise TossError(r.status_code, body.get("code", "UNKNOWN"), body.get("message", ""))
    return body
