-- Apply after schema.sql, as database owner. Transactional; no automatic identity matching.
begin;
create schema if not exists passo_private;
revoke all on schema passo_private from public, anon, authenticated;

create table public.auth_identity_links (
  auth_user_id uuid primary key references auth.users(id) on delete cascade,
  legacy_user_id bigint not null unique references public.users(id) on delete cascade,
  created_at timestamptz not null default now()
);
alter table public.auth_identity_links enable row level security;
revoke all on public.auth_identity_links from public, anon, authenticated;

-- Intentionally no is_master bypass. Only a provisioned identity + live session is trusted.
create function passo_private.current_user_id() returns bigint
language sql stable security definer set search_path = '' as $$
  select l.legacy_user_id from public.auth_identity_links l
  join auth.users u on u.id = l.auth_user_id
  join auth.sessions s on s.user_id = u.id
  where u.id = auth.uid() and s.id::text = (auth.jwt()->>'session_id')
    and (s.not_after is null or s.not_after > now())
    and (u.banned_until is null or u.banned_until < now())
    and coalesce((auth.jwt()->>'exp')::numeric, 0) > extract(epoch from now())
$$;
create function passo_private.tenant_role(tid bigint) returns text
language sql stable security definer set search_path = '' as $$
  select case when exists (
    select 1 from public.tenants t join public.org_members m on m.org_id = t.org_id
    where t.id = tid and m.user_id = passo_private.current_user_id() and m.role = '주인'
  ) then '주인' else (
    select role from public.memberships where tenant_id = tid and user_id = passo_private.current_user_id()
  ) end
$$;
create function public.passo_access(tid bigint, operation text default 'read') returns boolean
language sql stable security definer set search_path = '' as $$
  select coalesce(case operation
    when 'read' then passo_private.tenant_role(tid) is not null
    when 'review' then passo_private.tenant_role(tid) in ('주인', '확인하는 사람')
    when 'upload' then passo_private.tenant_role(tid) in ('주인', '확인하는 사람', '올리는 사람')
    else false end, false)
$$;
create function public.passo_session_active() returns boolean
language sql stable security definer set search_path = '' as $$
  select passo_private.current_user_id() is not null
$$;
create function public.passo_workspaces() returns table(id bigint, title text, role text)
language sql stable security definer set search_path = '' as $$
  select t.id, coalesce(t.title, t.name), passo_private.tenant_role(t.id)
  from public.tenants t where public.passo_access(t.id, 'read') order by t.id
$$;
revoke all on all functions in schema passo_private from public, anon, authenticated;
revoke all on function public.passo_access(bigint,text), public.passo_session_active(), public.passo_workspaces() from public, anon;
grant execute on function public.passo_access(bigint,text), public.passo_session_active(), public.passo_workspaces() to authenticated;

-- Abort instead of accidentally combining unknown permissive policies with these rules.
do $$
declare t text;
begin
  foreach t in array array['users','orgs','org_members','memberships','invites','tenants',
    'object_types','property_defs','alias_dict','link_types','entities','entity_aliases','entity_links',
    'source_inputs','records','proposals','widgets','automations','chat_sessions','eval_votes','ai_logs'] loop
    if exists(select 1 from pg_policies where schemaname='public' and tablename=t) then
      raise exception 'Existing policies require review on %', t;
    end if;
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on public.%I from public, anon, authenticated', t);
  end loop;
  foreach t in array array['object_types','property_defs','alias_dict','link_types','entities',
    'entity_aliases','entity_links','source_inputs','records','proposals','widgets','automations'] loop
    execute format('grant select on public.%I to authenticated', t);
    execute format('create policy passo_read on public.%I for select to authenticated using (public.passo_access(tenant_id))', t);
  end loop;
end $$;
grant select on public.tenants to authenticated;
create policy passo_read on public.tenants for select to authenticated using (public.passo_access(id));
-- Chat, logs, user passwords, invites and membership tables remain server-only.

-- Row lock + compare-and-swap prevents lost edits and preserves other cell states.
-- No direct authenticated UPDATE grants, including column-level grants.
create function public.passo_edit_cells(tid bigint, rid bigint, expected_data jsonb, changes jsonb)
returns setof public.records language plpgsql security definer set search_path = '' as $$
declare current_data jsonb; k text; v jsonb; normalized jsonb;
begin
  if not public.passo_access(tid, 'review') then raise insufficient_privilege; end if;
  select data into current_data from public.records where id=rid and tenant_id=tid for update;
  if not found then return; end if;
  if current_data is distinct from expected_data then raise exception 'Record changed' using errcode='40001'; end if;
  if jsonb_typeof(changes) <> 'object' or changes = '{}'::jsonb or octet_length(changes::text)>65536 then
    raise exception 'Invalid changes' using errcode='22023';
  end if;
  for k,v in select * from jsonb_each(changes) loop
    if not (current_data ? k) or length(k)>200 or jsonb_typeof(v) not in ('string','number','null') then
      raise exception 'Invalid cell' using errcode='22023';
    end if;
    normalized := case when jsonb_typeof(v)='string' then to_jsonb(btrim(v #>> '{}', E' \t\r\n')) else v end;
    current_data := jsonb_set(current_data, array[k], jsonb_build_object(
      'version','cell-state-v1','raw',v,
      'value',case when normalized in ('null'::jsonb,'""'::jsonb,'"-"'::jsonb) then 'null'::jsonb else normalized end,
      'status',case when normalized='null'::jsonb then 'unknown' when normalized='""'::jsonb then 'blank'
        when normalized='"-"'::jsonb then 'explicit_dash' else 'value' end));
  end loop;
  return query update public.records set data=current_data where id=rid and tenant_id=tid returning *;
end $$;
revoke all on function public.passo_edit_cells(bigint,bigint,jsonb,jsonb) from public, anon;
grant execute on function public.passo_edit_cells(bigint,bigint,jsonb,jsonb) to authenticated;

-- New job/checkpoint foundation: users can read metadata, only a future worker writes.
alter table public.source_inputs add constraint source_inputs_tenant_id_id_unique unique(tenant_id,id);
create table public.passo_jobs (
 id uuid primary key default gen_random_uuid(), tenant_id bigint not null references public.tenants(id) on delete cascade,
 source_input_id bigint, status text not null default 'queued' check(status in ('queued','running','awaiting_review','succeeded','failed','canceled')),
 created_at timestamptz not null default now(), unique(tenant_id,id),
 foreign key(tenant_id,source_input_id) references public.source_inputs(tenant_id,id) on delete cascade
);
create table public.passo_checkpoints (
 tenant_id bigint not null, job_id uuid not null, revision bigint not null, state jsonb not null,
 primary key(tenant_id,job_id,revision), foreign key(tenant_id,job_id) references public.passo_jobs(tenant_id,id) on delete cascade
);
alter table public.passo_jobs enable row level security;
alter table public.passo_checkpoints enable row level security;
revoke all on public.passo_jobs, public.passo_checkpoints from public, anon, authenticated;
grant select on public.passo_jobs to authenticated;
create policy passo_read on public.passo_jobs for select to authenticated using(public.passo_access(tenant_id));
-- Raw checkpoints deliberately unavailable to end users, including same-tenant users.

-- A new private bucket avoids opening legacy unscoped object paths.
insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
values ('passo-originals','passo-originals',false,20971520,array['image/jpeg','image/png','application/pdf']);
create function public.passo_object_access(object_name text, operation text) returns boolean
language plpgsql stable security definer set search_path = '' as $$
declare tid bigint;
begin
 if object_name !~ '^[1-9][0-9]*/[0-9a-f-]{36}/[^/]+$' or object_name like '%..%' then return false; end if;
 begin tid := split_part(object_name,'/',1)::bigint; exception when numeric_value_out_of_range then return false; end;
 return public.passo_access(tid,operation);
end $$;
revoke all on function public.passo_object_access(text,text) from public,anon;
grant execute on function public.passo_object_access(text,text) to authenticated;
-- RESTRICTIVE guard prevents other permissive bucket policies from bypassing ours.
create policy passo_storage_guard on storage.objects as restrictive for all to public
 using(bucket_id <> 'passo-originals' or public.passo_object_access(name,'read'))
 with check(bucket_id <> 'passo-originals' or public.passo_object_access(name,'upload'));
create policy passo_storage_read on storage.objects for select to authenticated
 using(bucket_id='passo-originals' and public.passo_object_access(name,'read'));
create policy passo_storage_insert on storage.objects for insert to authenticated
 with check(bucket_id='passo-originals' and public.passo_object_access(name,'upload'));
-- Guard even if a deployment already has broad permissive UPDATE/DELETE policies.
create policy passo_storage_no_update on storage.objects as restrictive for update to public
 using(bucket_id <> 'passo-originals') with check(bucket_id <> 'passo-originals');
create policy passo_storage_no_delete on storage.objects as restrictive for delete to public
 using(bucket_id <> 'passo-originals');
grant all on public.auth_identity_links, public.passo_jobs, public.passo_checkpoints to service_role;
commit;
