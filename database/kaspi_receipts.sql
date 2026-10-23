create table if not exists public.kaspi_receipts (
    id uuid primary key default gen_random_uuid(),
    external_receipt_id text unique,
    idempotency_key text not null unique,
    amount numeric(18,2) not null check (amount > 0),
    approved_amount numeric(18,2) check (approved_amount is null or approved_amount > 0),
    currency text not null default 'KZT' check (currency = 'KZT'),
    submitted_at timestamptz,
    customer_reference text,
    receipt_type text not null check (receipt_type in ('file_id', 'file_url')),
    receipt_value text not null,
    telegram_user_id bigint,
    metadata jsonb not null default '{}'::jsonb,
    status text not null default 'pending' check (status in ('pending', 'confirmed', 'rejected')),
    decision text check (decision is null or decision in ('confirmed', 'rejected')),
    operator_id bigint,
    comment text,
    created_at timestamptz not null default now(),
    decided_at timestamptz,
    next_reminder_at timestamptz default (now() + interval '2 minutes'),
    reminder_count integer not null default 0 check (reminder_count >= 0),
    last_notification_error text,
    operator_notified_at timestamptz,
    webhook_event_id uuid,
    webhook_delivered_at timestamptz,
    webhook_next_attempt_at timestamptz,
    webhook_attempts integer not null default 0 check (webhook_attempts >= 0),
    webhook_last_error text
);

create index if not exists kaspi_receipts_pending_reminders_idx
    on public.kaspi_receipts (next_reminder_at)
    where status = 'pending';
create index if not exists kaspi_receipts_customer_created_idx
    on public.kaspi_receipts (telegram_user_id, created_at desc);

create table if not exists public.kaspi_balance_transactions (
    id uuid primary key default gen_random_uuid(),
    receipt_id uuid not null unique references public.kaspi_receipts(id),
    type text not null default 'receipt_credit' check (type = 'receipt_credit'),
    amount numeric(18,2) not null check (amount > 0),
    currency text not null default 'KZT' check (currency = 'KZT'),
    operator_id bigint not null,
    created_at timestamptz not null default now()
);
create index if not exists kaspi_balance_transactions_created_idx
    on public.kaspi_balance_transactions (created_at desc, id desc);

alter table public.kaspi_receipts enable row level security;
alter table public.kaspi_balance_transactions enable row level security;
revoke all on public.kaspi_receipts, public.kaspi_balance_transactions from public, anon, authenticated;
grant select, insert, update, delete on public.kaspi_receipts, public.kaspi_balance_transactions to service_role;

create or replace function public.create_kaspi_receipt(
    p_external_receipt_id text,
    p_idempotency_key text,
    p_amount numeric,
    p_currency text,
    p_submitted_at timestamptz,
    p_customer_reference text,
    p_receipt_type text,
    p_receipt_value text,
    p_telegram_user_id bigint,
    p_metadata jsonb
) returns setof public.kaspi_receipts
language plpgsql
security invoker
set search_path = public
as $$
begin
    if p_idempotency_key is null or length(trim(p_idempotency_key)) = 0 then
        raise exception 'idempotency key is required';
    end if;
    if p_amount is null or p_amount <= 0 or upper(coalesce(p_currency, 'KZT')) <> 'KZT' then
        raise exception 'amount must be positive and currency must be KZT';
    end if;
    if p_receipt_type not in ('file_id', 'file_url') or p_receipt_value is null or length(trim(p_receipt_value)) = 0 then
        raise exception 'invalid receipt file';
    end if;

    insert into public.kaspi_receipts (
        external_receipt_id, idempotency_key, amount, currency, submitted_at,
        customer_reference, receipt_type, receipt_value, telegram_user_id, metadata
    ) values (
        nullif(p_external_receipt_id, ''), trim(p_idempotency_key), p_amount, 'KZT', p_submitted_at,
        p_customer_reference, p_receipt_type, p_receipt_value, p_telegram_user_id, coalesce(p_metadata, '{}'::jsonb)
    ) on conflict do nothing;

    return query
        select r.* from public.kaspi_receipts r
        where r.idempotency_key = trim(p_idempotency_key)
           or (p_external_receipt_id is not null and r.external_receipt_id = p_external_receipt_id)
        order by (r.idempotency_key = trim(p_idempotency_key)) desc
        limit 1;
end;
$$;

create or replace function public.decide_kaspi_receipt(
    p_receipt_id uuid,
    p_decision text,
    p_approved_amount numeric,
    p_operator_id bigint,
    p_comment text
) returns jsonb
language plpgsql
security invoker
set search_path = public
as $$
declare
    v_receipt public.kaspi_receipts%rowtype;
    v_transaction_id uuid;
begin
    if p_decision not in ('confirmed', 'rejected') then
        return jsonb_build_object('ok', false, 'error', 'invalid_decision');
    end if;
    if p_operator_id is null then
        return jsonb_build_object('ok', false, 'error', 'operator_required');
    end if;
    if p_decision = 'confirmed' and (p_approved_amount is null or p_approved_amount <= 0) then
        return jsonb_build_object('ok', false, 'error', 'approved_amount_required');
    end if;

    select * into v_receipt
      from public.kaspi_receipts
     where id = p_receipt_id
     for update;
    if not found then
        return jsonb_build_object('ok', false, 'error', 'not_found');
    end if;
    if v_receipt.status <> 'pending' then
        return jsonb_build_object('ok', true, 'already_decided', true, 'receipt_id', v_receipt.id, 'status', v_receipt.status);
    end if;

    update public.kaspi_receipts
       set status = p_decision,
           decision = p_decision,
           approved_amount = case when p_decision = 'confirmed' then p_approved_amount else null end,
           operator_id = p_operator_id,
           comment = nullif(trim(coalesce(p_comment, '')), ''),
           decided_at = now(),
           next_reminder_at = null,
           last_notification_error = null
     where id = p_receipt_id
     returning * into v_receipt;

    if p_decision = 'confirmed' then
        insert into public.kaspi_balance_transactions (receipt_id, amount, currency, operator_id)
        values (v_receipt.id, p_approved_amount, 'KZT', p_operator_id)
        on conflict (receipt_id) do nothing
        returning id into v_transaction_id;
        if v_transaction_id is null then
            select id into v_transaction_id from public.kaspi_balance_transactions where receipt_id = v_receipt.id;
        end if;
    end if;

    return jsonb_build_object(
        'ok', true,
        'already_decided', false,
        'receipt_id', v_receipt.id,
        'status', v_receipt.status,
        'approved_amount', v_receipt.approved_amount,
        'balance_transaction_id', v_transaction_id,
        'decided_at', v_receipt.decided_at
    );
end;
$$;

create or replace function public.claim_due_kaspi_receipts(p_limit integer default 50)
returns setof public.kaspi_receipts
language sql
security invoker
set search_path = public
as $$
    with due as (
        select id
          from public.kaspi_receipts
         where status = 'pending' and next_reminder_at <= now()
         order by next_reminder_at
         for update skip locked
         limit greatest(1, least(coalesce(p_limit, 50), 100))
    )
    update public.kaspi_receipts r
       set next_reminder_at = now() + interval '2 minutes',
           reminder_count = reminder_count + 1
      from due
     where r.id = due.id
    returning r.*;
$$;

create or replace function public.kaspi_balance_summary()
returns table(currency text, confirmed_total numeric, updated_at timestamptz, balance_type text)
language sql
security invoker
set search_path = public
as $$
    select 'KZT'::text, coalesce(sum(amount), 0)::numeric, max(created_at), 'internal_ledger'::text
      from public.kaspi_balance_transactions;
$$;

revoke all on function public.create_kaspi_receipt(text,text,numeric,text,timestamptz,text,text,text,bigint,jsonb) from public, anon, authenticated;
revoke all on function public.decide_kaspi_receipt(uuid,text,numeric,bigint,text) from public, anon, authenticated;
revoke all on function public.claim_due_kaspi_receipts(integer) from public, anon, authenticated;
revoke all on function public.kaspi_balance_summary() from public, anon, authenticated;
grant execute on function public.create_kaspi_receipt(text,text,numeric,text,timestamptz,text,text,text,bigint,jsonb) to service_role;
grant execute on function public.decide_kaspi_receipt(uuid,text,numeric,bigint,text) to service_role;
grant execute on function public.claim_due_kaspi_receipts(integer) to service_role;
grant execute on function public.kaspi_balance_summary() to service_role;

