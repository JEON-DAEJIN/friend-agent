"""로그 한 줄을 JSON으로 남긴다.

Cloud Run은 JSON 로그의 severity 값을 읽어 로그 화면의 심각도(정보, 경고, 오류)로 보여 준다.
키나 문의 원문 같은 값은 로그에 남기지 않는다.
"""
import json
import sys


def log(severity, message, **fields):
    record = {"severity": severity, "message": message, **fields}
    print(json.dumps(record, ensure_ascii=False), file=sys.stdout, flush=True)


# 결제 뒤 토스가 돌려보내는 주소(/payments/success?paymentKey=…&orderId=…&amount=…)가 웹 서버(uvicorn)의
# 접속 로그에 그대로 찍혀 paymentKey 전체가 남는 것을 N056 실습에서 확인했다. 접속 로그의 주소에서 paymentKey 는
# 앞 4자만 남기고 가린다(시크릿 키와 함께면 결제 조회·취소에 쓰이는 값이다).
# 한계: Cloud Run 앞단(Google 프런트엔드)의 요청 로그는 앱 밖에서 남으므로 여기서 가리지 못한다.
import logging  # noqa: E402
import re  # noqa: E402

_PAYMENT_KEY = re.compile(r"(paymentKey=)([^&\s]{0,4})[^&\s]*")


def mask_payment_key(text):
    return _PAYMENT_KEY.sub(r"\1\2***", text)


class MaskPaymentKey(logging.Filter):
    def filter(self, record):
        if isinstance(record.args, tuple):
            record.args = tuple(mask_payment_key(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.msg, str):
            record.msg = mask_payment_key(record.msg)
        return True


def install_access_log_mask():
    logging.getLogger("uvicorn.access").addFilter(MaskPaymentKey())
