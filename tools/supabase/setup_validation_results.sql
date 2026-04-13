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

create index if not exists idx_validation_results_ts
  on public.validation_results (validation_ts desc);

create index if not exists idx_validation_results_src_tgt
  on public.validation_results (src_table_name, tgt_table_name);

alter table public.validation_results enable row level security;

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
