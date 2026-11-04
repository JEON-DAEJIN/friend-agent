-- 크레딧 결제 주문(N056). 0002_credits.sql 다음에 SQL Editor에서 한 번 실행한다(staging·production 각각).
--
-- 결제를 시작하기 전에 주문을 먼저 저장하고(pending), 결제 승인이 성공한 뒤에만 paid 로 바꾼다.
-- 금액·크레딧 수는 서버가 상품표에서 찾아 넣는다(화면이 보낸 금액을 쓰지 않는다). 승인할 때 이 금액과 비교한다.
-- RLS를 켜고 정책은 만들지 않는다. 서버(secret key)만 읽고 쓴다.

create table if not exists public.orders (
  order_id text primary key,                 -- 추측할 수 없는 무작위 값(ord_…), 토스 결제의 orderId
  user_id uuid not null references auth.users (id) on delete cascade,
  product_id text not null,
  amount integer not null check (amount > 0),
  credits integer not null check (credits > 0),
  status text not null default 'pending' check (status in ('pending', 'paid', 'failed')),
  payment_key text,                           -- 승인 뒤 토스의 paymentKey (로그에는 전체를 남기지 않는다)
  approved_at timestamptz,                    -- 토스가 승인한 시각. 문서용 테스트 키에서는 이것이 승인의 기록이다
  failure_code text,                          -- 실패 페이지나 승인 거절의 오류 코드
  created_at timestamptz not null default now()
);
create index if not exists orders_user_idx on public.orders (user_id, created_at);

alter table public.orders enable row level security;
