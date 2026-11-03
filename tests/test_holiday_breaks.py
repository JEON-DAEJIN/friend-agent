"""공휴일 도구가 연휴(공휴일과 주말이 이어져 3일 이상 쉬는 구간)를 계산해 결과에 넣는다(N055 4장).

답 모델이 요일을 보고 연휴를 직접 판단하다 틀리던 것을 코드가 맡는다. 가짜 공휴일 표를 쓰므로 인터넷을 부르지 않는다.
2026-12-25 금, 2026-12-31 목, 2027-01-01 금, 2027-03-01 월, 2026-11-04 수.
"""
from datetime import date
from types import SimpleNamespace

import pytest

from app import tools

CTX = SimpleNamespace(timezone="Asia/Seoul")


def fake(monkeypatch, table, broken=()):
    def holidays_of(year):
        if year in broken:
            raise RuntimeError("연결 안 됨")
        return [(d, n) for d, n in table if d.year == year]

    monkeypatch.setattr(tools, "holidays_of", holidays_of)


def run(frm, to):
    return tools.get_holidays({"from": frm, "to": to}, CTX)


def test_friday_holiday_makes_a_three_day_break(monkeypatch):
    fake(monkeypatch, [(date(2026, 12, 25), "크리스마스")])
    r = run("2026-12-01", "2026-12-31")
    assert "12월 25일(금)~12월 27일(일) 3일" in r.summary
    assert r.data["long_breaks"] == [{"from": "2026-12-25", "to": "2026-12-27", "days": 3}]


def test_monday_holiday_takes_in_the_weekend_before_it(monkeypatch):
    fake(monkeypatch, [(date(2027, 3, 1), "3·1절")])
    assert "2월 27일(토)~3월 1일(월) 3일" in run("2027-03-01", "2027-03-31").summary


def test_lunar_new_year_on_saturday_to_tuesday_is_four_days(monkeypatch):
    fake(monkeypatch, [(date(2027, 2, 6), "설날"), (date(2027, 2, 8), "설날"), (date(2027, 2, 9), "설날")])
    r = run("2027-02-01", "2027-02-28")
    assert "2월 6일(토)~2월 9일(화) 4일" in r.summary and r.summary.count("3일 이상 연휴") == 1


def test_a_midweek_holiday_is_not_a_break(monkeypatch):
    fake(monkeypatch, [(date(2026, 11, 4), "임시")])
    r = run("2026-11-02", "2026-11-08")
    assert "3일 이상 이어지는 연휴는 없습니다" in r.summary and r.data["long_breaks"] == []


def test_other_holidays_are_said_not_to_be_breaks(monkeypatch):
    fake(monkeypatch, [(date(2026, 12, 25), "크리스마스"), (date(2026, 12, 23), "임시")])
    s = run("2026-12-01", "2026-12-31").summary
    assert "12월 25일(금)~12월 27일(일) 3일" in s and "나머지 공휴일은 주말과 이어져도 3일이 안 됩니다" in s


def test_a_break_crossing_the_range_edge_uses_the_neighbouring_year(monkeypatch):
    fake(monkeypatch, [(date(2026, 12, 31), "임시"), (date(2027, 1, 1), "새해")])
    s = run("2027-01-01", "2027-01-01").summary
    assert "12월 31일(목)~1월 3일(일) 4일" in s


def test_if_the_neighbouring_year_cannot_be_read_that_day_counts_as_a_weekday(monkeypatch):
    fake(monkeypatch, [(date(2026, 12, 31), "임시"), (date(2027, 1, 1), "새해")], broken=(2026,))
    assert "1월 1일(금)~1월 3일(일) 3일" in run("2027-01-01", "2027-01-01").summary


def test_a_year_inside_the_range_that_cannot_be_read_still_fails(monkeypatch):
    fake(monkeypatch, [], broken=(2027,))
    with pytest.raises(RuntimeError):
        run("2027-01-01", "2027-01-31")


def test_next_holiday_outside_the_range_also_says_whether_it_is_a_break(monkeypatch):
    fake(monkeypatch, [(date(2026, 12, 25), "크리스마스")])
    r = run("2026-11-02", "2026-11-30")
    assert "12월 25일(금) 크리스마스입니다" in r.summary and "12월 25일(금)~12월 27일(일) 3일 연휴" in r.summary
    assert r.data["next_holiday_after"] == {"date": "2026-12-25", "name": "크리스마스"}
