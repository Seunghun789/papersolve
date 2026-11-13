-- Durable upload reservation, fenced worker leases, checkpoints and atomic publication.
begin;
alter table public.passo_jobs drop constraint passo_jobs_status_check;
alter table public.passo_jobs add constraint passo_jobs_status_check check(status in ('uploading','queued','running','awaiting_review','succeeded','failed','canceled'));
alter table public.passo_jobs
 add column request_key uuid,
 add column sha256 text,
 add column media_type text,
 add column byte_size bigint,
 add column attempts integer not null default 0,
 add column lease_token uuid,
 add column lease_until timestamptz,
 add column error_code text,
 add column updated_at timestamptz not null default now(),
 add constraint passo_jobs_request_unique unique(tenant_id,request_key);
alter table public.records add column source_row integer;
create unique index passo_record_source_row on public.records(source_input_id,source_row) where source_row is not null;
create index passo_jobs_claim on public.passo_jobs(status,lease_until,created_at);

create function public.passo_reserve_document(tid bigint, request_key uuid, digest text, mime text, size_bytes bigint, filename text)
returns public.passo_jobs language plpgsql security definer set search_path='' as $$
declare j public.passo_jobs; sid bigint; jid uuid := gen_random_uuid(); ext text;
begin
 if not public.passo_access(tid,'upload') then raise insufficient_privilege; end if;
 if request_key is null or digest !~ '^[0-9a-f]{64}$' or size_bytes not between 1 and 10485760
    or mime not in ('image/png','image/jpeg','application/pdf') or length(filename) not between 1 and 200
    or digest is null or mime is null or size_bytes is null or filename is null then
   raise exception 'Invalid upload' using errcode='22023';
 end if;
 -- Serializes identical reservations, without orphan source rows under concurrent retries.
 perform pg_advisory_xact_lock(hashtextextended(tid::text || request_key::text,0));
 select * into j from public.passo_jobs where tenant_id=tid and passo_jobs.request_key=passo_reserve_document.request_key;
 if found then
  if j.sha256<>digest or j.media_type<>mime or j.byte_size<>size_bytes then
   raise exception 'Idempotency key reused' using errcode='PT409';
  end if;
  return j;
 end if;
 ext := case mime when 'image/png' then 'png' when 'image/jpeg' then 'jpg' else 'pdf' end;
 insert into public.source_inputs(tenant_id,channel,name,storage_path,created_by)
 values(tid,case when mime='application/pdf' then '파일' else '사진' end,filename,
 tid::text||'/'||jid::text||'/original.'||ext,passo_private.current_user_id()) returning id into sid;
 insert into public.passo_jobs(id,tenant_id,source_input_id,status,request_key,sha256,media_type,byte_size)
 values(jid,tid,sid,'uploading',request_key,digest,mime,size_bytes) returning * into j;
 return j;
end $$;

create function public.passo_enqueue_document(tid bigint,jid uuid) returns public.passo_jobs
language plpgsql security definer set search_path='' as $$
declare j public.passo_jobs;
begin
 if not public.passo_access(tid,'upload') then raise insufficient_privilege; end if;
 select * into j from public.passo_jobs where tenant_id=tid and id=jid for update;
 if not found then raise no_data_found; end if;
 if j.status='uploading' then
  if not exists(select 1 from storage.objects o join public.source_inputs s on s.storage_path=o.name
   where s.id=j.source_input_id and s.tenant_id=tid and o.bucket_id='passo-originals') then
   raise exception 'Original missing' using errcode='PT409';
  end if;
  update public.passo_jobs set status='queued',updated_at=now() where id=jid returning * into j;
 end if;
 return j;
end $$;

-- Only service_role may call worker functions. A new token fences every reclaimed job.
create function public.passo_claim_document() returns setof public.passo_jobs
language plpgsql security definer set search_path='' as $$
declare jid uuid;
begin
 update public.passo_jobs set status='failed',error_code='attempts_exhausted',lease_token=null,lease_until=null,updated_at=now()
 where status='running' and lease_until<now() and attempts>=3;
 select id into jid from public.passo_jobs
 where request_key is not null and (status='queued' or (status='running' and lease_until<now())) and attempts<3
 order by created_at for update skip locked limit 1;
 if jid is null then return; end if;
 return query update public.passo_jobs set status='running',attempts=attempts+1,
 lease_token=gen_random_uuid(),lease_until=now()+interval '120 seconds',updated_at=now(),error_code=null
 where id=jid returning *;
end $$;

create function public.passo_worker_save(jid uuid, fence uuid, checkpoint jsonb default null)
returns boolean language plpgsql security definer set search_path='' as $$
declare j public.passo_jobs;
begin
 select * into j from public.passo_jobs where id=jid for update;
 if not found or j.status<>'running' or j.lease_token is distinct from fence or j.lease_until<now() then return false; end if;
 if checkpoint is not null then
  if octet_length(checkpoint::text)>8388608 then raise exception 'Checkpoint too large'; end if;
  insert into public.passo_checkpoints(tenant_id,job_id,revision,state)
  select j.tenant_id,jid,coalesce(max(revision),0)+1,checkpoint from public.passo_checkpoints where job_id=jid;
 end if;
 update public.passo_jobs set lease_until=now()+interval '120 seconds',updated_at=now() where id=jid;
 return true;
end $$;

create function public.passo_worker_finish(jid uuid,fence uuid, payload jsonb)
returns boolean language plpgsql security definer set search_path='' as $$
declare j public.passo_jobs; r jsonb; n integer := 0;
begin
 select * into j from public.passo_jobs where id=jid for update;
 if not found or j.status<>'running' or j.lease_token is distinct from fence or j.lease_until<now() then return false; end if;
 if jsonb_typeof(payload->'records') is distinct from 'array' or jsonb_array_length(payload->'records') not between 1 and 1000 then
  raise exception 'Invalid result';
 end if;
 for r in select * from jsonb_array_elements(payload->'records') loop
  insert into public.records(tenant_id,type_name,occurred_at,data,uncertain,source_input_id,source_row,status)
  values(j.tenant_id,r->>'type_name',(r->>'occurred_at')::date,r->'data',coalesce(r->'uncertain','[]'),j.source_input_id,n,'대기');
  n := n+1;
 end loop;
 update public.source_inputs set spans=payload->'spans' where id=j.source_input_id and tenant_id=j.tenant_id;
 update public.passo_jobs set status='awaiting_review',lease_token=null,lease_until=null,updated_at=now() where id=jid;
 return true;
end $$;

create function public.passo_worker_fail(jid uuid,fence uuid) returns boolean
language plpgsql security definer set search_path='' as $$
begin
 update public.passo_jobs set status=case when attempts<3 then 'queued' else 'failed' end,
 error_code='processing_failed',lease_token=null,lease_until=null,updated_at=now()
 where id=jid and status='running' and lease_token=fence and lease_until>now();
 return found;
end $$;

create function public.passo_confirm_document(tid bigint,jid uuid) returns public.passo_jobs
language plpgsql security definer set search_path='' as $$
declare j public.passo_jobs;
begin
 if not public.passo_access(tid,'review') then raise insufficient_privilege; end if;
 select * into j from public.passo_jobs where id=jid and tenant_id=tid for update;
 if not found then raise no_data_found; end if;
 if j.status='succeeded' then return j; end if;
 if j.status<>'awaiting_review' then raise exception 'Not ready' using errcode='PT409'; end if;
 update public.records set status='확정' where tenant_id=tid and source_input_id=j.source_input_id;
 update public.passo_jobs set status='succeeded',updated_at=now() where id=jid returning * into j;
 return j;
end $$;

-- Lease tokens and upload hashes are worker-only, not public job metadata.
revoke select on public.passo_jobs from authenticated;
grant select(id,tenant_id,source_input_id,status,created_at,updated_at,attempts,error_code) on public.passo_jobs to authenticated;
revoke all on function public.passo_reserve_document(bigint,uuid,text,text,bigint,text),public.passo_enqueue_document(bigint,uuid),public.passo_confirm_document(bigint,uuid) from public,anon;
grant execute on function public.passo_reserve_document(bigint,uuid,text,text,bigint,text),public.passo_enqueue_document(bigint,uuid),public.passo_confirm_document(bigint,uuid) to authenticated;
revoke all on function public.passo_claim_document(),public.passo_worker_save(uuid,uuid,jsonb),public.passo_worker_finish(uuid,uuid,jsonb),public.passo_worker_fail(uuid,uuid) from public,anon,authenticated;
grant execute on function public.passo_claim_document(),public.passo_worker_save(uuid,uuid,jsonb),public.passo_worker_finish(uuid,uuid,jsonb),public.passo_worker_fail(uuid,uuid) to service_role;
notify pgrst,'reload schema';
commit;
