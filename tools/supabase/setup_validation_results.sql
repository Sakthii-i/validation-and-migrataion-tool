-- Run this once in Supabase SQL Editor.
-- Creates the table required by React /api validation result persistence.

create extension if not exists pgcrypto;

create table if not exists public.validation_results (
  validation_id text primary key,
  validation_ts timestamptz not null default now(),
  validation_type text,
  src_table_name text,
  tgt_table_name text,
  row_count text,
  schema_check text,
  numeric_check text,
  hash_validation text,
  details jsonb not null default '{}'::jsonb
);

create table if not exists public.validation_results_bigquery
  (like public.validation_results including all);

alter table public.validation_results add column if not exists run_by text;
alter table public.validation_results_bigquery add column if not exists run_by text;

create table if not exists public.query_dashboard_stats (
  source_engine text primary key,
  total_queries_processed integer not null default 0,
  successful_migrations integer not null default 0,
  validated_queries integer not null default 0,
  simple_queries integer not null default 0,
  medium_queries integer not null default 0,
  complex_queries integer not null default 0,
  updated_at timestamptz not null default now()
);

create table if not exists public.query_history_bigquery (
  query_id text primary key,
  query_name text,
  source_engine text not null default 'bigquery',
  run_by text,
  last_ran_ts timestamptz not null default now(),
  source_latency_ms integer,
  target_latency_ms integer,
  migration_mode text,
  validation_status text,
  pushed_to_git boolean not null default false,
  reviewers text[] default '{}',
  source_sql text,
  translated_sql text,
  details jsonb not null default '{}'::jsonb
);

create table if not exists public.query_history_snowflake (
  query_id text primary key,
  query_name text,
  source_engine text not null default 'snowflake',
  run_by text,
  last_ran_ts timestamptz not null default now(),
  source_latency_ms integer,
  target_latency_ms integer,
  migration_mode text,
  validation_status text,
  pushed_to_git boolean not null default false,
  reviewers text[] default '{}',
  source_sql text,
  translated_sql text,
  details jsonb not null default '{}'::jsonb
);

create table if not exists public.query_history_trino (
  query_id text primary key,
  query_name text,
  source_engine text not null default 'trino',
  run_by text,
  last_ran_ts timestamptz not null default now(),
  source_latency_ms integer,
  target_latency_ms integer,
  migration_mode text,
  validation_status text,
  pushed_to_git boolean not null default false,
  reviewers text[] default '{}',
  source_sql text,
  translated_sql text,
  details jsonb not null default '{}'::jsonb
);

create table if not exists public.query_history_redshift (
  query_id text primary key,
  query_name text,
  source_engine text not null default 'redshift',
  run_by text,
  last_ran_ts timestamptz not null default now(),
  source_latency_ms integer,
  target_latency_ms integer,
  migration_mode text,
  validation_status text,
  pushed_to_git boolean not null default false,
  reviewers text[] default '{}',
  source_sql text,
  translated_sql text,
  details jsonb not null default '{}'::jsonb
);

create table if not exists public.validation_results_trino
  (like public.validation_results including all);

create table if not exists public.validation_results_redshift
  (like public.validation_results including all);

alter table public.validation_results_trino add column if not exists run_by text;
alter table public.validation_results_redshift add column if not exists run_by text;

create index if not exists idx_validation_results_ts
  on public.validation_results (validation_ts desc);

create index if not exists idx_validation_results_src_tgt
  on public.validation_results (src_table_name, tgt_table_name);

create index if not exists idx_validation_results_bigquery_ts
  on public.validation_results_bigquery (validation_ts desc);

create index if not exists idx_validation_results_bigquery_src_tgt
  on public.validation_results_bigquery (src_table_name, tgt_table_name);

create index if not exists idx_query_history_bigquery_ts
  on public.query_history_bigquery (last_ran_ts desc);

create index if not exists idx_query_history_snowflake_ts
  on public.query_history_snowflake (last_ran_ts desc);

create index if not exists idx_query_history_trino_ts
  on public.query_history_trino (last_ran_ts desc);

create index if not exists idx_query_history_redshift_ts
  on public.query_history_redshift (last_ran_ts desc);

create index if not exists idx_validation_results_trino_ts
  on public.validation_results_trino (validation_ts desc);

create index if not exists idx_validation_results_trino_src_tgt
  on public.validation_results_trino (src_table_name, tgt_table_name);

create index if not exists idx_validation_results_redshift_ts
  on public.validation_results_redshift (validation_ts desc);

create index if not exists idx_validation_results_redshift_src_tgt
  on public.validation_results_redshift (src_table_name, tgt_table_name);

alter table public.validation_results enable row level security;
alter table public.validation_results_bigquery enable row level security;
alter table public.validation_results_trino enable row level security;
alter table public.validation_results_redshift enable row level security;
alter table public.query_dashboard_stats enable row level security;
alter table public.query_history_bigquery enable row level security;
alter table public.query_history_snowflake enable row level security;
alter table public.query_history_trino enable row level security;
alter table public.query_history_redshift enable row level security;

-- Backend uses publishable key through PostgREST; allow authenticated and anon roles.
do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'validation_results' and policyname = 'validation_results_select_all'
  ) then
    create policy validation_results_select_all
      on public.validation_results
      for select
      to anon, authenticated
      using (true);
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'validation_results' and policyname = 'validation_results_insert_all'
  ) then
    create policy validation_results_insert_all
      on public.validation_results
      for insert
      to anon, authenticated
      with check (true);
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'validation_results' and policyname = 'validation_results_update_all'
  ) then
    create policy validation_results_update_all
      on public.validation_results
      for update
      to anon, authenticated
      using (true)
      with check (true);
  end if;
end$$;

do $$
declare
  tbl text;
begin
  foreach tbl in array array['validation_results_bigquery', 'validation_results_trino', 'validation_results_redshift', 'query_dashboard_stats', 'query_history_bigquery', 'query_history_snowflake', 'query_history_trino', 'query_history_redshift']
  loop
    if not exists (
      select 1 from pg_policies
      where schemaname = 'public' and tablename = tbl and policyname = tbl || '_select_all'
    ) then
      execute format('create policy %I on public.%I for select to anon, authenticated using (true)', tbl || '_select_all', tbl);
    end if;

    if not exists (
      select 1 from pg_policies
      where schemaname = 'public' and tablename = tbl and policyname = tbl || '_insert_all'
    ) then
      execute format('create policy %I on public.%I for insert to anon, authenticated with check (true)', tbl || '_insert_all', tbl);
    end if;

    if not exists (
      select 1 from pg_policies
      where schemaname = 'public' and tablename = tbl and policyname = tbl || '_update_all'
    ) then
      execute format('create policy %I on public.%I for update to anon, authenticated using (true) with check (true)', tbl || '_update_all', tbl);
    end if;
  end loop;
end$$;

-- Migration: add source/target latency columns to existing tables
alter table public.query_history_bigquery add column if not exists source_latency_ms integer;
alter table public.query_history_bigquery add column if not exists target_latency_ms integer;
alter table public.query_history_bigquery add column if not exists reviewers text[] default '{}';
alter table public.query_history_snowflake add column if not exists source_latency_ms integer;
alter table public.query_history_snowflake add column if not exists target_latency_ms integer;
alter table public.query_history_trino add column if not exists source_latency_ms integer;
alter table public.query_history_trino add column if not exists target_latency_ms integer;
alter table public.query_history_trino add column if not exists reviewers text[] default '{}';
alter table public.query_history_redshift add column if not exists source_latency_ms integer;
alter table public.query_history_redshift add column if not exists target_latency_ms integer;
alter table public.query_history_redshift add column if not exists reviewers text[] default '{}';
alter table public.query_history_snowflake add column if not exists reviewers text[] default '{}';

-- =========================================================
-- EXPLICIT GRANTS FOR SUPABASE DATA API
-- =========================================================

-- validation_results
grant select, insert, update, delete
on public.validation_results
to anon, authenticated;

grant all
on public.validation_results
to service_role;

-- validation_results_bigquery
grant select, insert, update, delete
on public.validation_results_bigquery
to anon, authenticated;

grant all
on public.validation_results_bigquery
to service_role;

-- validation_results_trino
grant select, insert, update, delete
on public.validation_results_trino
to anon, authenticated;

grant all
on public.validation_results_trino
to service_role;

-- validation_results_redshift
grant select, insert, update, delete
on public.validation_results_redshift
to anon, authenticated;

grant all
on public.validation_results_redshift
to service_role;

-- query_dashboard_stats
grant select, insert, update, delete
on public.query_dashboard_stats
to anon, authenticated;

grant all
on public.query_dashboard_stats
to service_role;

-- query_history_bigquery
grant select, insert, update, delete
on public.query_history_bigquery
to anon, authenticated;

grant all
on public.query_history_bigquery
to service_role;

-- query_history_snowflake
grant select, insert, update, delete
on public.query_history_snowflake
to anon, authenticated;

grant all
on public.query_history_snowflake
to service_role;

-- query_history_trino
grant select, insert, update, delete
on public.query_history_trino
to anon, authenticated;

grant all
on public.query_history_trino
to service_role;

-- query_history_redshift
grant select, insert, update, delete
on public.query_history_redshift
to anon, authenticated;

grant all
on public.query_history_redshift
to service_role;
