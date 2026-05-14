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
  source_sql text,
  translated_sql text,
  details jsonb not null default '{}'::jsonb
);

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

alter table public.validation_results enable row level security;
alter table public.validation_results_bigquery enable row level security;
alter table public.query_dashboard_stats enable row level security;
alter table public.query_history_bigquery enable row level security;
alter table public.query_history_snowflake enable row level security;

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
  foreach tbl in array array['validation_results_bigquery', 'query_dashboard_stats', 'query_history_bigquery', 'query_history_snowflake']
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
alter table public.query_history_snowflake add column if not exists source_latency_ms integer;
alter table public.query_history_snowflake add column if not exists target_latency_ms integer;
