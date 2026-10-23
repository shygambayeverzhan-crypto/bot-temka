create table if not exists public.kaspi_operators (
    telegram_id bigint primary key,
    username text,
    first_name text,
    status text not null default 'pending'
        check (status in ('pending', 'active', 'disabled')),
    requested_at timestamptz not null default now(),
    approved_by bigint,
    approved_at timestamptz,
    updated_at timestamptz not null default now()
);

alter table public.kaspi_operators enable row level security;
revoke all on public.kaspi_operators from public, anon, authenticated;
grant select, insert, update, delete on public.kaspi_operators to service_role;

insert into public.kaspi_operators (telegram_id, status, approved_by, approved_at)
values (6575928505, 'active', 6575928505, now())
on conflict (telegram_id) do update
set status = 'active',
    approved_by = 6575928505,
    approved_at = coalesce(public.kaspi_operators.approved_at, now()),
    updated_at = now();

