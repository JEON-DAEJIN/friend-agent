"""트레이스로 가는 글의 개인정보 가리기(N055 6장). 가려져야 하는 것과 **가려지면 안 되는 것**을 함께 지킨다.

기억에 저장하지 않는 목록(safety.SENSITIVE_PATTERNS: 주민번호·카드·계좌·비밀번호)과 트레이스 마스킹의 범위가 어긋나 있던 것을 맞춘다.
"""
import pytest

from app import observe


@pytest.mark.parametrize("text, hidden, label", [
    ("계좌 110-123-456789 로 보내 줘", "110-123-456789", "[계좌번호]"),
    ("주민번호는 901231-1234567", "901231-1234567", "[주민번호]"),
    ("카드는 1234-5678-9012-3456", "1234-5678-9012-3456", "[카드번호]"),
    ("내 번호 010-1234-5678", "010-1234-5678", "[전화번호]"),
    ("유선은 02-123-4567", "02-123-4567", "[전화번호]"),
    ("메일 a.b@example.com", "a.b@example.com", "[이메일]"),
    ("비밀번호는 abc1234야", "abc1234", "[비밀번호]"),
    ("password: hunter2", "hunter2", "[비밀번호]"),
])
def test_personal_data_is_hidden_with_its_own_label(text, hidden, label):
    masked = observe.mask(data=text)
    assert hidden not in masked and label in masked


@pytest.mark.parametrize("text", [
    "2026-11-03에 시험 봐",
    "2026-11-02부터 2026-11-30까지 공휴일이 없습니다.",
    "알림은 2026-10-14T21:00:00+09:00 에 보낸다",
    "12월 25일(금)~12월 27일(일) 3일",
    "접수번호 12345 확인",
    "비밀번호 잊어버렸어",          # 값이 아니라 푸념
    "비밀번호가 뭐였지?",
    "오늘 3시에 만나",
])
def test_ordinary_text_and_dates_are_left_alone(text):
    assert observe.mask(data=text) == text


def test_password_word_stays_so_the_trace_still_reads_naturally():
    assert observe.mask(data="내 비밀번호는 abc1234야") == "내 비밀번호는 [비밀번호]"


def test_masks_inside_nested_data_and_other_types_pass_through():
    data = {"a": ["110-123-456789", {"b": "901231-1234567"}], "n": 3, "ok": True}
    assert observe.mask(data=data) == {"a": ["[계좌번호]", {"b": "[주민번호]"}], "n": 3, "ok": True}
