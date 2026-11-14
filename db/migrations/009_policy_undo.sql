-- Versioned decisions and reversible record changes. No historical rows are rewritten.
begin;
create table public.passo_audit (
 id bigint generated always as identity primary key,
 tenant_id bigint not null references public.tenants(id) on delete cascade,
 actor_id bigint references public.users(id), operation text not null,
 before_state jsonb not null, after_state jsonb not null,
 reversible boolean not null default false, undone_at timestamptz,
 undone_by bigint references public.users(id), created_at timestamptz not null default now()
);
alter table public.passo_audit enable row level security;
revoke all on public.passo_audit from public,anon,authenticated;
grant select on public.passo_audit to authenticated;
create policy audit_read on public.passo_audit for select to authenticated using(public.passo_access(tenant_id,'read'));

create function passo_private.audit(tid bigint,op text,b jsonb,a jsonb,can_undo boolean default false)
returns bigint language plpgsql set search_path='' as $$
declare aid bigint;
begin
 insert into public.passo_audit(tenant_id,actor_id,operation,before_state,after_state,reversible)
 values(tid,passo_private.current_user_id(),op,b,a,can_undo) returning id into aid;
 return aid;
end $$;

create function passo_private.source_snapshot(tid bigint,sid bigint)
returns jsonb language sql stable set search_path='' as $$
 select jsonb_build_object('source',to_jsonb(s),'records',coalesce((select jsonb_agg(to_jsonb(r) order by r.id) from public.records r where r.tenant_id=tid and r.source_input_id=sid),'[]'),
 'pending',coalesce((select jsonb_agg(to_jsonb(q) order by q.id) from public.proposals q where q.tenant_id=tid and q.source_input_id=sid and q.status='대기'),'[]'))
 from public.source_inputs s where s.tenant_id=tid and s.id=sid;
$$;
create function public.passo_source_preview(tid bigint,sid bigint)
returns jsonb language plpgsql security definer set search_path='' as $$
declare s jsonb;
begin
 if not public.passo_access(tid,'read') then raise insufficient_privilege; end if;
 s:=passo_private.source_snapshot(tid,sid);
 if s is null then raise exception 'Missing source' using errcode='22023'; end if;
 return s||jsonb_build_object('revision',md5(s::text),'can_reopen',
 (s->'source'->>'auto')::boolean and (s->'source'->>'created_at')::timestamptz>=now()-interval '7 days'
 and exists(select 1 from jsonb_array_elements(s->'records') r where r->>'status'='확정'));
end $$;
create function public.passo_reopen_source(tid bigint,sid bigint,expected_revision text)
returns jsonb language plpgsql security definer set search_path='' as $$
declare s jsonb; qid bigint;
begin
 if not public.passo_access(tid,'review') then raise insufficient_privilege; end if;
 perform 1 from public.tenants where id=tid for update;
 perform 1 from public.source_inputs where id=sid and tenant_id=tid for update;
 perform 1 from public.records where source_input_id=sid and tenant_id=tid order by id for update;
 s:=public.passo_source_preview(tid,sid);
 if expected_revision is distinct from s->>'revision' or not(s->>'can_reopen')::boolean then raise exception 'Source changed or undo window expired' using errcode='PT409'; end if;
 update public.records set status='대기' where tenant_id=tid and source_input_id=sid and status='확정';
 update public.source_inputs set auto=false where tenant_id=tid and id=sid;
 insert into public.proposals(tenant_id,kind,payload,evidence,source_input_id,created_by)
 values(tid,'읽은 값',jsonb_build_object('type',s->'records'->0->>'type_name','rows',jsonb_array_length(s->'records'),'reopened',true),'{"user":true}',sid,passo_private.current_user_id()) returning id into qid;
 perform passo_private.audit(tid,'문서 다시 확인',s,passo_private.source_snapshot(tid,sid));
 return jsonb_build_object('review_id',qid);
end $$;

-- Keep checked v1 bodies private; wrappers capture complete before/after values atomically.
alter function public.passo_edit_cells(bigint,bigint,jsonb,jsonb) set schema passo_private;
alter function passo_private.passo_edit_cells(bigint,bigint,jsonb,jsonb) rename to edit_cells_v1;
create function public.passo_edit_cells(tid bigint,rid bigint,expected_data jsonb,changes jsonb)
returns setof public.records language plpgsql security definer set search_path='' as $$
declare b jsonb; r public.records;
begin
 if not public.passo_access(tid,'review') then raise insufficient_privilege; end if;
 perform 1 from public.tenants where id=tid for update;
 select to_jsonb(x) into b from public.records x where x.tenant_id=tid and x.id=rid for update;
 for r in select * from passo_private.edit_cells_v1(tid,rid,expected_data,changes) loop
  if b is distinct from to_jsonb(r) then
   perform passo_private.audit(tid,'셀 수정',jsonb_build_object('records',jsonb_build_array(b)),jsonb_build_object('records',jsonb_build_array(to_jsonb(r))),true);
  end if;
  return next r;
 end loop;
end $$;

create function public.passo_answer_read(tid bigint,qid bigint,expected_revision text,answer text)
returns public.proposals language plpgsql security definer set search_path='' as $$
declare q public.proposals; s jsonb; b jsonb;
begin
 if not public.passo_access(tid,'review') then raise insufficient_privilege; end if;
 perform 1 from public.tenants where id=tid for update;
 select * into q from public.proposals where tenant_id=tid and id=qid for update;
 if not found or q.kind<>'읽은 값' then raise exception 'Missing read review' using errcode='22023'; end if;
 perform 1 from public.source_inputs where tenant_id=tid and id=q.source_input_id for update;
 perform 1 from public.records where tenant_id=tid and source_input_id=q.source_input_id order by id for update;
 s:=passo_private.source_snapshot(tid,q.source_input_id);
 if q.status<>'대기' or expected_revision is distinct from md5(s::text) then raise exception 'Read context changed' using errcode='PT409'; end if;
 if answer is null or answer not in ('accept','reject') then raise exception 'Invalid answer' using errcode='22023'; end if;
 b:=jsonb_build_object('records',s->'records','proposal',to_jsonb(q));
 update public.proposals set status=case when answer='accept' then '승인' else '거절' end, decided_at=now(),decided_by=passo_private.current_user_id(),
 payload=payload||jsonb_build_object('answer',answer) where tenant_id=tid and id=qid returning * into q;
 if answer='reject' then
  update public.records set status='거절' where tenant_id=tid and source_input_id=q.source_input_id and status='대기';
 elsif not exists(select 1 from public.proposals where tenant_id=tid and source_input_id=q.source_input_id and status='대기' and kind in ('종류','칸','같은 칸','읽은 값')) then
  update public.records r set status=case when exists(select 1 from public.object_types o where o.tenant_id=tid and o.kind='기록' and o.name=r.type_name) then '확정' else '거절' end
  where r.tenant_id=tid and r.source_input_id=q.source_input_id and r.status='대기';
 end if;
 s:=passo_private.source_snapshot(tid,q.source_input_id);
 perform passo_private.audit(tid,'문서 확인',b,jsonb_build_object('records',s->'records','proposal',to_jsonb(q)),true);
 return q;
end $$;

create function public.passo_undo(tid bigint,aid bigint)
returns boolean language plpgsql security definer set search_path='' as $$
declare a public.passo_audit; item jsonb; current_value jsonb; before_item jsonb; q public.proposals;
begin
 if not public.passo_access(tid,'review') then raise insufficient_privilege; end if;
 perform 1 from public.tenants where id=tid for update;
 select * into a from public.passo_audit where tenant_id=tid and id=aid for update;
 if not found or not a.reversible or a.undone_at is not null then raise exception 'Not reversible' using errcode='PT409'; end if;
 -- Full-row CAS includes uncertainty and publication state, not only the displayed value.
 for item in select * from jsonb_array_elements(a.after_state->'records') loop
  select to_jsonb(r) into current_value from public.records r where r.tenant_id=tid and r.id=(item->>'id')::bigint for update;
  if current_value is distinct from item then raise exception 'Later record changes prevent undo' using errcode='PT409'; end if;
 end loop;
 if a.after_state ? 'proposal' then
  select * into q from public.proposals where tenant_id=tid and id=(a.after_state->'proposal'->>'id')::bigint for update;
  if not found or to_jsonb(q) is distinct from a.after_state->'proposal' then raise exception 'Later review changes prevent undo' using errcode='PT409'; end if;
 end if;
 for before_item in select * from jsonb_array_elements(a.before_state->'records') loop
  update public.records set data=before_item->'data',uncertain=before_item->'uncertain',status=before_item->>'status'
   where tenant_id=tid and id=(before_item->>'id')::bigint;
 end loop;
 if a.before_state ? 'proposal' then
  update public.proposals set status='대기',decided_at=null,decided_by=null,payload=(a.before_state->'proposal'->'payload')||'{"reopened":true}'::jsonb
   where tenant_id=tid and id=q.id;
 end if;
 update public.passo_audit set undone_at=now(),undone_by=passo_private.current_user_id() where tenant_id=tid and id=aid;
 perform passo_private.audit(tid,'되돌리기',a.after_state,a.before_state||jsonb_build_object('audit_id',aid));
 return true;
end $$;

create function passo_private.cell_value(v jsonb)
returns jsonb language sql immutable set search_path='' as $$
 select case when jsonb_typeof(v)='object' then case when v->>'status'='value' then v->'value' else null end else nullif(v,'null'::jsonb) end;
$$;
create function passo_private.auto_decision(tid bigint,q public.proposals)
returns jsonb language plpgsql stable set search_path='' as $$
declare p public.property_defs; unit_old text; unit_new text; m_old numeric; m_new numeric; n integer; reason text; action text:='accept'; a text; b text; t text;
begin
 if q.status<>'대기' then reason:='이미 답한 항목';
 elsif coalesce((q.evidence->>'user')::boolean,false) or coalesce((q.evidence->>'no_ai')::boolean,false) or coalesce((q.payload->>'reopened')::boolean,false) then reason:='사람이 요청하거나 다시 연 항목';
 elsif q.kind in ('종류','칸') then null;
 elsif q.kind='같은 칸' then
  select * into p from public.property_defs where tenant_id=tid and type_name=q.payload->>'type' and name=q.payload->>'similar_to';
  if not found then reason:='기존 칸을 찾을 수 없음';
  elsif p.dtype is distinct from q.payload->>'dtype' then action:='reject';
  else
   unit_old:=replace(replace(lower(btrim(coalesce(p.unit,''))),' ',''),'°c','℃');
   unit_new:=replace(replace(lower(btrim(coalesce(q.payload->>'unit',''))),' ',''),'°c','℃');
   if unit_old<>'' and unit_new<>'' and unit_old<>unit_new then reason:='단위가 다름';
   elsif p.dtype='number' then
    select count(*),percentile_cont(0.5) within group(order by v) into n,m_old from (
     select case when passo_private.cell_value(e.value)#>>'{}' ~ '^-?[0-9]+([.][0-9]+)?$' then abs((passo_private.cell_value(e.value)#>>'{}')::numeric) end v from public.records r cross join lateral jsonb_each(r.data) e
     where r.tenant_id=tid and r.type_name=p.type_name and r.status='확정'
      and (e.key=p.name or exists(select 1 from public.alias_dict d where d.tenant_id=tid and d.type_name=p.type_name and d.alias=e.key and d.prop_name=p.name))
      and passo_private.cell_value(e.value)#>>'{}' ~ '^-?[0-9]+([.][0-9]+)?$'
    ) nums where v>0;
    select percentile_cont(0.5) within group(order by v) into m_new from (
     select case when x#>>'{}' ~ '^-?[0-9]+([.][0-9]+)?$' then abs((x#>>'{}')::numeric) end v
      from jsonb_array_elements(coalesce(q.evidence->'samples','[]')) x) nums where v>0;
    if n>=3 and m_new>0 and greatest(m_old,m_new)/least(m_old,m_new)>=10 then reason:='기존 값과 크기가 10배 이상 다름'; end if;
   end if;
  end if;
 elsif q.kind='애매한 값' then
  select p0.* into p from public.property_defs p0 where p0.tenant_id=tid and p0.type_name=q.payload->>'type'
   and p0.name=coalesce((select d.prop_name from public.alias_dict d where d.tenant_id=tid and d.type_name=p0.type_name and d.alias=q.payload->>'label'),q.payload->>'label');
  if not found or p.dtype in ('number','date') or q.payload ? 'over' then reason:='합계·기간 또는 기준에 영향을 주는 값';
  elsif q.evidence->>'reason' in ('missing','unknown','unreadable') then reason:='누락·미확인·판독 불가';
  elsif exists(select 1 from public.source_inputs s cross join lateral jsonb_array_elements(coalesce(s.spans->(q.payload->>'label'),'[]')) sp
    cross join lateral jsonb_array_elements_text(coalesce(sp->'cand','[]')) c
    where s.tenant_id=tid and s.id=q.source_input_id and regexp_replace(c,'[^0-9]','','g')<>''
    and position(regexp_replace(c,'[^0-9]','','g') in regexp_replace(coalesce(q.payload->>'value',''),'[^0-9]','','g'))=0) then reason:='숫자가 다른 후보 값';
  else action:='unconfirmed'; end if;
 elsif q.kind='연결' then
  t:=split_part(q.payload->>'from','.',1); a:=substr(q.payload->>'from',length(t)+2); b:=substr(q.payload->>'to',length(t)+2);
  if split_part(q.payload->>'to','.',1)<>t or a='' or b='' or not exists(
   select 1 from public.records r cross join lateral jsonb_each(r.data) x cross join lateral jsonb_each(r.data) y
   where r.tenant_id=tid and r.type_name=t and r.status='확정'
   and (x.key=a or exists(select 1 from public.alias_dict d where d.tenant_id=tid and d.type_name=t and d.alias=x.key and d.prop_name=a))
   and (y.key=b or exists(select 1 from public.alias_dict d where d.tenant_id=tid and d.type_name=t and d.alias=y.key and d.prop_name=b))
   and passo_private.cell_value(x.value) is not null and passo_private.cell_value(y.value) is not null
  ) then reason:='같은 기록에 함께 나온 근거가 없음'; end if;
 elsif q.kind='읽은 값' then null;
 else reason:='사람 확인이 필요한 제안';
 end if;
 return jsonb_build_object('eligible',reason is null,'action',action,'reason',coalesce(reason,case action when 'unconfirmed' then '글자는 미확인 상태를 유지' when 'reject' then '값 종류가 달라 별도 칸 생성' else '정책 조건 통과' end),'version','auto-v1');
end $$;

create function passo_private.apply_structure(tid bigint,qid bigint,expected_revision text,answer text)
returns public.proposals language plpgsql security definer set search_path='' as $$
declare q public.proposals; t text; target text; result_status text;
begin

 q:=passo_private.lock_structure(tid,qid,expected_revision);
 if answer is null or answer not in ('accept','reject','split') or (answer='split' and q.kind<>'종류') then raise exception 'Invalid answer' using errcode='22023'; end if;
 t:=q.payload->>'type';
 if jsonb_typeof(q.payload->'type') is distinct from 'string' or length(btrim(t)) not between 1 and 200 then raise exception 'Invalid type' using errcode='22023'; end if;
 if q.kind='종류' and answer<>'reject' then
  insert into public.object_types(tenant_id,name,kind) values(tid,t,'기록') on conflict(tenant_id,name) do nothing;
 end if;
 if (q.kind<>'종류' or answer<>'reject') and not exists(select 1 from public.object_types where tenant_id=tid and name=t and kind='기록') then
  raise exception 'Record type missing or conflicts with entity' using errcode='22023';
 end if;
 if q.kind='종류' and answer<>'reject' then
  perform passo_private.structure_type(tid,q,answer);
 elsif q.kind='칸' then
  if answer='accept' then perform passo_private.structure_property(tid,t,q.payload,q.source_input_id);
  else perform passo_private.structure_alias(tid,t,q.payload->>'label',null);
  end if;
 elsif q.kind='같은 칸' then
  if answer='accept' then
   target:=q.payload->>'similar_to';
   if not exists(select 1 from public.property_defs where tenant_id=tid and type_name=t and name=target) then raise exception 'Target property missing' using errcode='22023'; end if;
   perform passo_private.structure_alias(tid,t,q.payload->>'label',target);
  else
   target:=q.payload->>'label';
   if target=q.payload->>'similar_to' then target:=target||' (2)'; end if;
   perform passo_private.structure_property(tid,t,q.payload||jsonb_build_object('name',target,'label',target),q.source_input_id);
   perform passo_private.structure_alias(tid,t,q.payload->>'label',target);
  end if;
 end if;
 result_status:=case answer when 'reject' then '거절' when 'split' then '고침' else '승인' end;
 update public.proposals set status=result_status,decided_by=passo_private.current_user_id(),decided_at=now(),
  payload=payload||jsonb_build_object('answer',answer)||case when q.kind='같은 칸' then jsonb_build_object('made',case when answer='reject' then target else null end) else '{}'::jsonb end
  where id=qid and tenant_id=tid returning * into q;
 -- Legacy waiting records settle only after all structural blockers for this source close.
 if q.source_input_id is not null and not exists(select 1 from public.proposals where tenant_id=tid and source_input_id=q.source_input_id and status='대기' and kind in ('종류','칸','같은 칸','읽은 값')) then
  update public.records r set status=case when exists(select 1 from public.object_types o where o.tenant_id=tid and o.name=r.type_name and o.kind='기록') then '확정' else '거절' end
   where r.tenant_id=tid and r.source_input_id=q.source_input_id and r.status='대기';
 end if;
 return q;
end $$;
create function passo_private.auto_apply(tid bigint,sid bigint)
returns jsonb language plpgsql set search_path='' as $$
declare q public.proposals; d jsonb; decided public.proposals; applied integer:=0; held integer:=0; failures integer:=0; pass integer;
begin
 perform 1 from public.tenants where id=tid for update;
 for pass in 1..8 loop
  <<nloop>> declare changed integer:=0; begin
   for q in select * from public.proposals where tenant_id=tid and source_input_id=sid and status='대기' order by case kind when '종류' then 1 when '칸' then 2 when '같은 칸' then 3 when '읽은 값' then 4 when '연결' then 6 else 5 end,id for update loop
    begin
     d:=passo_private.auto_decision(tid,q);
     if not(d->>'eligible')::boolean then continue; end if;
     if q.kind in ('종류','칸','같은 칸') then
      decided:=passo_private.apply_structure(tid,q.id,md5(passo_private.structure_snapshot(tid,q.id)::text),d->>'action');
     elsif q.kind='연결' then
      insert into public.link_types(tenant_id,from_ref,to_ref,kind,reason,name)
       values(tid,q.payload->>'from',q.payload->>'to',q.payload->>'kind',q.payload->>'reason',q.payload->>'name') on conflict(tenant_id,from_ref,to_ref,kind) do nothing;
      update public.proposals set status='승인',decided_at=now() where tenant_id=tid and id=q.id;
     else
      update public.proposals set status='승인',decided_at=now(),payload=payload||case when d->>'action'='unconfirmed' then '{"unconfirmed":true}'::jsonb else '{}'::jsonb end where tenant_id=tid and id=q.id;
     end if;
     update public.proposals set payload=payload||jsonb_build_object('policy',d) where tenant_id=tid and id=q.id returning * into decided;
     perform passo_private.audit(tid,'자동 판단',jsonb_build_object('proposal',to_jsonb(q)),jsonb_build_object('proposal',to_jsonb(decided),'policy',d));
     changed:=changed+1;applied:=applied+1;
    exception when others then
     -- This subtransaction rolls back all changes to this proposal. It stays pending.
     failures:=failures+1;
    end;
   end loop;
   if changed=0 then exit; end if;
  end nloop;
 end loop;
 if not exists(select 1 from public.proposals where tenant_id=tid and source_input_id=sid and status='대기' and kind in ('종류','칸','같은 칸','읽은 값')) then
  update public.records r set status=case when exists(select 1 from public.object_types o where o.tenant_id=tid and o.name=r.type_name and o.kind='기록') then '확정' else '거절' end where r.tenant_id=tid and r.source_input_id=sid and r.status='대기';
 end if;
 select count(*) into held from public.proposals where tenant_id=tid and source_input_id=sid and status='대기';
 return jsonb_build_object('applied',applied,'pending',held,'failures',failures);
end $$;

create function public.passo_policy_preview(tid bigint,sid bigint)
returns jsonb language plpgsql security definer set search_path='' as $$
declare s jsonb; qs jsonb;
begin
 if not public.passo_access(tid,'read') then raise insufficient_privilege; end if;
 s:=passo_private.source_snapshot(tid,sid);
 if s is null then raise exception 'Missing source' using errcode='22023'; end if;
 select coalesce(jsonb_agg(jsonb_build_object('id',q.id,'kind',q.kind,'decision',passo_private.auto_decision(tid,q)) order by q.id),'[]') into qs from public.proposals q where q.tenant_id=tid and q.source_input_id=sid and q.status='대기';
 return jsonb_build_object('items',qs,'revision',md5(s::text||qs::text),'version','auto-v1');
end $$;
create function public.passo_apply_policy(tid bigint,sid bigint,expected_revision text)
returns jsonb language plpgsql security definer set search_path='' as $$
declare preview jsonb;
begin
 if not public.passo_access(tid,'review') then raise insufficient_privilege; end if;
 perform 1 from public.tenants where id=tid for update;
 perform 1 from public.source_inputs where tenant_id=tid and id=sid for update;
 perform 1 from public.records where tenant_id=tid order by id for update;
 preview:=public.passo_policy_preview(tid,sid);
 if expected_revision is distinct from preview->>'revision' then raise exception 'Policy context changed' using errcode='PT409'; end if;
 return passo_private.auto_apply(tid,sid);
end $$;

-- Apply the policy during the worker's existing atomic publication transaction.
alter function public.passo_worker_finish(uuid,uuid,jsonb) set schema passo_private;
alter function passo_private.passo_worker_finish(uuid,uuid,jsonb) rename to worker_finish_v1;
create function public.passo_worker_finish(jid uuid,fence uuid,payload jsonb)
returns boolean language plpgsql security definer set search_path='' as $$
declare j public.passo_jobs;
begin
 select * into j from public.passo_jobs where id=jid;
 if not found then return false; end if;
 perform 1 from public.tenants where id=j.tenant_id for update;
 if not passo_private.worker_finish_v1(jid,fence,payload) then return false; end if;
 perform passo_private.auto_apply(j.tenant_id,j.source_input_id);
 return true;
end $$;

revoke all on function passo_private.audit(bigint,text,jsonb,jsonb,boolean),passo_private.source_snapshot(bigint,bigint),passo_private.cell_value(jsonb),passo_private.auto_decision(bigint,public.proposals),passo_private.apply_structure(bigint,bigint,text,text),passo_private.auto_apply(bigint,bigint),passo_private.edit_cells_v1(bigint,bigint,jsonb,jsonb),passo_private.worker_finish_v1(uuid,uuid,jsonb) from public,anon,authenticated,service_role;
revoke all on function public.passo_source_preview(bigint,bigint),public.passo_reopen_source(bigint,bigint,text),public.passo_edit_cells(bigint,bigint,jsonb,jsonb),public.passo_answer_read(bigint,bigint,text,text),public.passo_undo(bigint,bigint),public.passo_policy_preview(bigint,bigint),public.passo_apply_policy(bigint,bigint,text) from public,anon;
grant execute on function public.passo_source_preview(bigint,bigint),public.passo_reopen_source(bigint,bigint,text),public.passo_edit_cells(bigint,bigint,jsonb,jsonb),public.passo_answer_read(bigint,bigint,text,text),public.passo_undo(bigint,bigint),public.passo_policy_preview(bigint,bigint),public.passo_apply_policy(bigint,bigint,text) to authenticated;
revoke all on function public.passo_worker_finish(uuid,uuid,jsonb) from public,anon,authenticated;
grant execute on function public.passo_worker_finish(uuid,uuid,jsonb) to service_role;
create function passo_private.answer_review_v1(tid bigint,qid bigint,expected_payload jsonb,expected_records jsonb,answer text,value jsonb default null)
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
    perform passo_private.edit_cells_v1(tid,rid,r.data,jsonb_build_object(label,new_value));
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
create or replace function public.passo_answer_review(tid bigint,qid bigint,expected_payload jsonb,expected_records jsonb,answer text,value jsonb default null)
returns public.proposals language plpgsql security definer set search_path='' as $$
declare b jsonb; a jsonb; q public.proposals; r public.proposals;
begin
 if not public.passo_access(tid,'review') then raise insufficient_privilege; end if;
 perform 1 from public.tenants where id=tid for update;
 select * into q from public.proposals where id=qid and tenant_id=tid for update;
 perform 1 from public.records where tenant_id=tid and source_input_id=q.source_input_id order by id for update;
 b:=jsonb_build_object('proposal',to_jsonb(q),'records',coalesce(passo_private.source_snapshot(tid,q.source_input_id)->'records','[]'));
 r:=passo_private.answer_review_v1(tid,qid,expected_payload,expected_records,answer,value);
 a:=jsonb_build_object('proposal',to_jsonb(r),'records',coalesce(passo_private.source_snapshot(tid,q.source_input_id)->'records','[]'));
 perform passo_private.audit(tid,'확인 답변',b,a,q.kind='애매한 값');
 return r;
end $$;
revoke all on function passo_private.answer_review_v1(bigint,bigint,jsonb,jsonb,text,jsonb) from public,anon,authenticated,service_role;

commit;
