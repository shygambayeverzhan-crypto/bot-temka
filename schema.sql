create table if not exists business_users (
 id uuid primary key default gen_random_uuid(), telegram_id bigint unique not null,
 username text, first_name text, last_name text, created_at timestamptz default now(), updated_at timestamptz default now()
);
create table if not exists transactions (
 id uuid primary key default gen_random_uuid(), user_id uuid not null references business_users(id) on delete cascade,
 type text not null check(type in ('income','expense')), amount numeric(14,2) not null check(amount>0),
 description text, created_at timestamptz default now()
);
create table if not exists clients (
 id uuid primary key default gen_random_uuid(), user_id uuid not null references business_users(id) on delete cascade,
 name text not null, phone text, note text, created_at timestamptz default now()
);
create table if not exists tasks (
 id uuid primary key default gen_random_uuid(), user_id uuid not null references business_users(id) on delete cascade,
 title text not null, due_date date, status text not null default 'open' check(status in ('open','done')),
 created_at timestamptz default now()
);
create index if not exists transactions_user_idx on transactions(user_id,created_at desc);
create index if not exists clients_user_idx on clients(user_id,created_at desc);
create index if not exists tasks_user_idx on tasks(user_id,status);
