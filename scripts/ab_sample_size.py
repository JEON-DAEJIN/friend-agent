"""A/B 테스트에 필요한 표본 수와 p값을 계산한다. N055 5장 5-4·5-5.

- required_n: 두 비율(별로예요 비율 p1 → p2)을 유의수준·검정력으로 가르는 데 그룹마다 필요한 피드백 수
- p_value: 두 그룹의 관찰값(별로예요 x개/피드백 n개)이 우연으로도 나올 만한 차이인지(양쪽 검정, 합동 비율 z 검정)
- days_needed: 하루 메시지·피드백 비율·B 배정 비율로 그 수를 모으는 데 걸리는 날
표준 라이브러리만 쓴다. 실행: python scripts/ab_sample_size.py [--messages-per-day 3.13 --feedback-rate 0.064 --b-share 0.5]
"""
import math
import sys
from statistics import NormalDist


def required_n(p1, p2, alpha=0.05, power=0.8):
    z = NormalDist().inv_cdf
    za, zb = z(1 - alpha / 2), z(power)
    return math.ceil((za + zb) ** 2 * (p1 * (1 - p1) + p2 * (1 - p2)) / (p1 - p2) ** 2)


def p_value(x1, n1, x2, n2):
    pooled = (x1 + x2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    zscore = (x1 / n1 - x2 / n2) / se
    return 2 * (1 - NormalDist().cdf(abs(zscore)))


def days_needed(n_per_group, messages_per_day, feedback_rate, b_share=0.5):
    """작은 쪽 그룹(B 배정 비율이 50% 이하면 B)에 필요한 피드백이 쌓이는 데 걸리는 날."""
    small = min(b_share, 1 - b_share)
    per_day = messages_per_day * feedback_rate * small
    return n_per_group / per_day if per_day else math.inf


def option(name, default):
    return float(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


def main():
    n = required_n(0.20, 0.15)
    print(f"1) 별로예요 20% → 15%를 가르려면 그룹마다 피드백 {n}개 필요 (유의수준 0.05, 검정력 0.8)")
    print(f"   하루 50건·피드백 10%·50:50이면 B 그룹에 하루 2.5건 → {days_needed(n, 50, 0.10, 0.5):.0f}일")
    print(f"   같은 조건에서 B를 10%만 배정하면 → {days_needed(n, 50, 0.10, 0.1):.0f}일")
    p_small = p_value(13, 60, 8, 54)
    p_large = p_value(13 * 10, 60 * 10, 8 * 10 + 1, 54 * 10)
    print(f"2) 별로예요 21.7%(13/60) vs 14.8%(8/54): p값 {p_small:.2f} → 0.05보다 커서 우연과 구별 못 함")
    print(f"   같은 비율이 10배 많은 표본이면(130/600 vs 81/540): p값 {p_large:.3f}")
    m, f, b = option("--messages-per-day", 94 / 30), option("--feedback-rate", 6 / 94), option("--b-share", 0.5)
    d = days_needed(n, m, f, b)
    print(f"3) 내 서비스(하루 {m:.2f}건, 피드백 {f:.1%}, B {b:.0%}): 그룹마다 {n}개를 모으려면 약 {d:,.0f}일 (= {d / 365:.0f}년)")


if __name__ == "__main__":
    main()
