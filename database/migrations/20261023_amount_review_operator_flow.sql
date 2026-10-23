alter table public.kaspi_receipts drop constraint if exists kaspi_receipts_status_check;
alter table public.kaspi_receipts add constraint kaspi_receipts_status_check
    check (status in ('pending', 'amount_review', 'confirmed', 'rejected'));

drop index if exists public.kaspi_receipts_pending_reminders_idx;
create index kaspi_receipts_pending_reminders_idx
    on public.kaspi_receipts (next_reminder_at)
    where status in ('pending', 'amount_review');

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

    select * into v_receipt from public.kaspi_receipts where id = p_receipt_id for update;
    if not found then
        return jsonb_build_object('ok', false, 'error', 'not_found');
    end if;
    if v_receipt.status not in ('pending', 'amount_review') then
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

    return jsonb_build_object('ok', true, 'already_decided', false, 'receipt_id', v_receipt.id,
        'status', v_receipt.status, 'approved_amount', v_receipt.approved_amount,
        'balance_transaction_id', v_transaction_id, 'decided_at', v_receipt.decided_at);
end;
$$;

create or replace function public.claim_due_kaspi_receipts(p_limit integer default 50)
returns setof public.kaspi_receipts
language sql
security invoker
set search_path = public
as $$
    with due as (
        select id from public.kaspi_receipts
         where status in ('pending', 'amount_review') and next_reminder_at <= now()
         order by next_reminder_at
         for update skip locked
         limit greatest(1, least(coalesce(p_limit, 50), 100))
    )
    update public.kaspi_receipts r
       set next_reminder_at = now() + interval '2 minutes',
           reminder_count = reminder_count + 1
      from due where r.id = due.id
    returning r.*;
$$;

revoke all on function public.decide_kaspi_receipt(uuid,text,numeric,bigint,text) from public, anon, authenticated;
revoke all on function public.claim_due_kaspi_receipts(integer) from public, anon, authenticated;
grant execute on function public.decide_kaspi_receipt(uuid,text,numeric,bigint,text) to service_role;
grant execute on function public.claim_due_kaspi_receipts(integer) to service_role;

