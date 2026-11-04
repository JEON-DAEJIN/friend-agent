-- 크레딧 원장(N056). 잔액 칸 하나를 고치지 않고, 크레딧이 늘고 준 기록을 한 줄씩 쌓기만 한다.
-- 잔액은 그 사용자의 delta 합계다. 0001_init.sql 다음에 SQL Editor에서 한 번 실행한다(staging·production 각각).
--
-- - ref(참조)는 유일하다: 같은 가입(signup:사용자ID)·같은 메시지(chat:…)·같은 결제(order:…)가 두 번 기록되지 않는다.
-- - 잘못 차감했으면 그 줄을 지우지 않고 되돌리는 줄(refund:…)을 새로 쌓는다.
-- - RLS를 켜고 정책은 만들지 않는다. 서버(secret key)만 읽고 쓴다.
-- - '내 데이터 삭제'(/api/data)는 이 표를 지우지 않는다. 대화 내용은 없고(변화량·이유·참조만),
--   지우면 가입 무료 크레딧이 다시 지급되고 산 크레딧도 사라지기 때문이다. 계정(auth.users)을 지우면 함께 지워진다.

create table if not exists public.credit_ledger (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users (id) on delete cascade,
  delta integer not null check (delta <> 0),
  reason text not null check (reason in ('signup', 'chat', 'refund', 'purchase', 'adjust')),
  ref text not null unique,
  created_at timestamptz not null default now()
);
create index if not exists credit_ledger_user_idx on public.credit_ledger (user_id, id);

alter table public.credit_ledger enable row level security;

-- 잔액 = 변화량 합계
create or replace function public.credit_balance(p_user uuid)
returns integer
language sql
stable
set search_path = public
as $$
  select coalesce(sum(delta), 0)::integer from public.credit_ledger where user_id = p_user;
$$;

-- 차감 한 번을 데이터베이스 함수 하나로 묶는다.
-- 같은 사용자의 요청 두 개가 동시에 와도 잔액 1을 둘 다 보고 통과하지 않도록, 사용자별 잠금을 건 뒤
-- ① 가입 무료 크레딧이 없으면 쌓고 ② 잔액을 보고 ③ 모자라지 않을 때만 −줄을 쌓는다.
create or replace function public.spend_credit(p_user uuid, p_amount integer, p_reason text, p_ref text, p_free integer)
returns table (ok boolean, balance integer)
language plpgsql
set search_path = public
as $$
declare
  current_balance integer;
begin
  perform pg_advisory_xact_lock(hashtextextended(p_user::text, 0));
  if p_free > 0 then
    insert into public.credit_ledger (user_id, delta, reason, ref)
    values (p_user, p_free, 'signup', 'signup:' || p_user::text)
    on conflict (ref) do nothing;
  end if;
  select coalesce(sum(delta), 0)::integer into current_balance from public.credit_ledger where user_id = p_user;
  if current_balance < p_amount then
    return query select false, current_balance;
    return;
  end if;
  insert into public.credit_ledger (user_id, delta, reason, ref) values (p_user, -p_amount, p_reason, p_ref);
  return query select true, current_balance - p_amount;
end;
$$;

-- 브라우저(anon·authenticated)가 publishable key로 함수를 부르지 못하게 막는다. 서버(service_role)만 부른다.
revoke all on function public.credit_balance(uuid) from public, anon, authenticated;
revoke all on function public.spend_credit(uuid, integer, text, text, integer) from public, anon, authenticated;
grant execute on function public.credit_balance(uuid) to service_role;
grant execute on function public.spend_credit(uuid, integer, text, text, integer) to service_role;
