-- Additive UI migration. Existing records and proposals are never bulk rewritten.
begin;
alter table public.source_inputs add column manual_request_key uuid;
create unique index source_manual_request_unique on public.source_inputs(tenant_id,manual_request_key) where manual_request_key is not null;

create function public.passo_write_record(tid bigint,request_key uuid,type_name text,occurred_at date,data jsonb)
returns public.records language plpgsql security definer set search_path='' as $$
declare sid bigint; r public.records; k text; v jsonb; normalized jsonb; cells jsonb := '{}';
begin
 if not public.passo_access(tid,'upload') then raise insufficient_privilege; end if;
 if request_key is null or occurred_at is null or jsonb_typeof(data) is distinct from 'object' or data='{}'::jsonb or octet_length(data::text)>65536
 or not exists(select 1 from public.object_types o where o.tenant_id=tid and o.name=type_name and o.kind='기록') then
  raise exception 'Invalid record' using errcode='22023';
 end if;
 for k,v in select * from jsonb_each(data) loop
  if jsonb_typeof(v) not in ('string','number','null') or not exists(select 1 from public.property_defs p where p.tenant_id=tid and p.type_name=passo_write_record.type_name and p.name=k) then
   raise exception 'Invalid field' using errcode='22023';
  end if;
  normalized:=case when jsonb_typeof(v)='string' then to_jsonb(btrim(v#>>'{}',E' \t\r\n')) else v end;
  cells:=cells||jsonb_build_object(k,jsonb_build_object('version','cell-state-v1','raw',v,
   'value',case when normalized in ('null'::jsonb,'""'::jsonb,'"-"'::jsonb) then 'null'::jsonb else normalized end,
   'status',case when normalized='null'::jsonb then 'unknown' when normalized='""'::jsonb then 'blank' when normalized='"-"'::jsonb then 'explicit_dash' else 'value' end));
 end loop;
 perform pg_advisory_xact_lock(hashtextextended(tid::text||request_key::text,0));
 select s.id into sid from public.source_inputs s where s.tenant_id=tid and s.manual_request_key=request_key;
 if found then
  select * into r from public.records where source_input_id=sid and tenant_id=tid;
  if r.data is distinct from cells or r.type_name<>type_name or r.occurred_at<>occurred_at then raise exception 'Request reused' using errcode='PT409'; end if;
  return r;
 end if;
 insert into public.source_inputs(tenant_id,channel,name,created_by,manual_request_key)
 values(tid,'직접 입력',type_name,passo_private.current_user_id(),request_key) returning id into sid;
 insert into public.records(tenant_id,type_name,occurred_at,data,status,source_input_id,source_row)
 values(tid,type_name,occurred_at,cells,'확정',sid,0) returning * into r;
 return r;
end $$;

create function public.passo_answer_review(tid bigint,qid bigint,expected_payload jsonb,expected_records jsonb,answer text,value jsonb default null)
returns public.proposals language plpgsql security definer set search_path='' as $$
declare q public.proposals; r public.records; rid bigint; ids jsonb; label text; new_value jsonb; target_status text;
begin
 if not public.passo_access(tid,'review') then raise insufficient_privilege; end if;
 select * into q from public.proposals where id=qid and tenant_id=tid for update;
 if not found then raise exception 'Missing review' using errcode='22023'; end if;
 if q.status<>'대기' or q.payload is distinct from expected_payload then raise exception 'Review changed' using errcode='PT409'; end if;
 if answer is null or answer not in ('accept','edit','blank','reject') then raise exception 'Invalid answer' using errcode='22023'; end if;
 if q.kind='애매한 값' then
  if answer='reject' then raise exception 'Supply corrected value or blank' using errcode='22023'; end if;
  label:=q.payload->>'label';
  ids:=coalesce(q.payload->'record_ids',jsonb_build_array(q.payload->'record_id'));
  if jsonb_typeof(ids) is distinct from 'array' or jsonb_array_length(ids) not between 1 and 1000 then raise exception 'Invalid records' using errcode='22023'; end if;
  for rid in select (x#>>'{}')::bigint from jsonb_array_elements(ids) x loop
   select * into r from public.records where id=rid and tenant_id=tid and source_input_id=q.source_input_id for update;
   if not found or not(r.data ? label) then raise exception 'Missing record' using errcode='22023'; end if;
   if r.data is distinct from expected_records->rid::text then raise exception 'Record changed' using errcode='PT409'; end if;
   if answer in ('edit','blank') then
    new_value:=case when answer='blank' then '""'::jsonb else value end;
    if new_value is null or jsonb_typeof(new_value) not in ('string','number','null') then raise exception 'Invalid value' using errcode='22023'; end if;
    perform public.passo_edit_cells(tid,rid,r.data,jsonb_build_object(label,new_value));
   end if;
   update public.records set uncertain=coalesce((select jsonb_agg(u) from jsonb_array_elements_text(uncertain) u where u<>label),'[]') where id=rid and tenant_id=tid;
  end loop;
 elsif q.kind='연결' then
  if answer not in ('accept','reject') then raise exception 'Invalid link answer' using errcode='22023'; end if;
  if answer='accept' then
   insert into public.link_types(tenant_id,from_ref,to_ref,kind,reason,name)
   values(tid,q.payload->>'from',q.payload->>'to',q.payload->>'kind',q.payload->>'reason',q.payload->>'name')
   on conflict(tenant_id,from_ref,to_ref,kind) do nothing;
  end if;
 else
  raise exception 'This review kind requires the legacy workflow' using errcode='22023';
 end if;
 target_status:=case answer when 'reject' then '거절' when 'edit' then '고침' when 'blank' then '고침' else '승인' end;
 update public.proposals set status=target_status,decided_by=passo_private.current_user_id(),decided_at=now(),
 payload=payload||jsonb_build_object('answer',answer,'answer_value',value) where id=qid and tenant_id=tid returning * into q;
 return q;
end $$;

-- Publish records immediately; unresolved extraction cells get individual exception cards.
-- This affects future queued jobs only. Historical rows/decisions remain untouched.
create or replace function public.passo_worker_finish(jid uuid,fence uuid,payload jsonb)
returns boolean language plpgsql security definer set search_path='' as $$
declare j public.passo_jobs; r jsonb; n integer:=0; rid bigint; k text; v jsonb; pos integer; dtype text;
begin
 select * into j from public.passo_jobs where id=jid for update;
 if not found or j.status<>'running' or j.lease_token is distinct from fence or j.lease_until<now() then return false; end if;
 if jsonb_typeof(payload->'records') is distinct from 'array' or jsonb_array_length(payload->'records') not between 1 and 1000 then raise exception 'Invalid result'; end if;
 for r in select * from jsonb_array_elements(payload->'records') loop
  insert into public.object_types(tenant_id,name,kind) values(j.tenant_id,r->>'type_name','기록') on conflict(tenant_id,name) do nothing;
  if not exists(select 1 from public.object_types where tenant_id=j.tenant_id and name=r->>'type_name' and kind='기록') then raise exception 'Type conflicts with entity'; end if;
  insert into public.records(tenant_id,type_name,occurred_at,data,uncertain,source_input_id,source_row,status)
  values(j.tenant_id,r->>'type_name',(r->>'occurred_at')::date,r->'data',coalesce(r->'uncertain','[]'),j.source_input_id,n,'확정') returning id into rid;
  pos:=0;
  for k,v in select * from jsonb_each(r->'data') loop
   dtype:=case when coalesce(v->>'value','') ~ '^-?[0-9]+([.][0-9]+)?$' then 'number' else 'text' end;
   insert into public.property_defs(tenant_id,type_name,name,dtype,position)
   values(j.tenant_id,r->>'type_name',k,dtype,pos) on conflict(tenant_id,type_name,name) do nothing;
   insert into public.alias_dict(tenant_id,type_name,alias,prop_name)
   values(j.tenant_id,r->>'type_name',k,k) on conflict(tenant_id,type_name,alias) do nothing;
   if v->>'status' in ('ambiguous','unreadable','unknown','missing') or coalesce(r->'uncertain','[]') ? k then
    insert into public.proposals(tenant_id,kind,payload,evidence,source_input_id)
    values(j.tenant_id,'애매한 값',jsonb_build_object('type',r->>'type_name','label',k,'value',v->'raw','record_id',rid),
      jsonb_build_object('reason',coalesce(v->>'status','uncertain')),j.source_input_id);
   end if;
   pos:=pos+1;
  end loop;
  n:=n+1;
 end loop;
 update public.source_inputs set spans=payload->'spans',auto=true where id=j.source_input_id and tenant_id=j.tenant_id;
 update public.passo_jobs set status='succeeded',lease_token=null,lease_until=null,updated_at=now() where id=jid;
 return true;
end $$;
revoke all on function public.passo_write_record(bigint,uuid,text,date,jsonb),public.passo_answer_review(bigint,bigint,jsonb,jsonb,text,jsonb) from public,anon;
grant execute on function public.passo_write_record(bigint,uuid,text,date,jsonb),public.passo_answer_review(bigint,bigint,jsonb,jsonb,text,jsonb) to authenticated;
commit;
