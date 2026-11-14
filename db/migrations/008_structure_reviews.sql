-- Human review of legacy structure proposals. No existing business rows are rewritten.
begin;

create function passo_private.structure_snapshot(tid bigint, qid bigint)
returns jsonb language sql stable set search_path='' as $$
 select jsonb_build_object('proposal',to_jsonb(q),
  'affected_count',(select count(*) from public.records r where r.tenant_id=tid and r.type_name=q.payload->>'type'),
  'objects',coalesce((select jsonb_agg(to_jsonb(o) order by o.id) from public.object_types o where o.tenant_id=tid and o.name=q.payload->>'type'),'[]'),
  'properties',coalesce((select jsonb_agg(to_jsonb(p) order by p.id) from public.property_defs p where p.tenant_id=tid and p.type_name=q.payload->>'type'),'[]'),
  'aliases',coalesce((select jsonb_agg(to_jsonb(a) order by a.id) from public.alias_dict a where a.tenant_id=tid and a.type_name=q.payload->>'type'),'[]'),
  'records',coalesce((select jsonb_agg(to_jsonb(r) order by r.id) from public.records r where r.tenant_id=tid and r.source_input_id=q.source_input_id),'[]'),
  'pending',coalesce((select jsonb_agg(jsonb_build_object('id',p.id,'kind',p.kind,'payload',p.payload) order by p.id) from public.proposals p where p.tenant_id=tid and p.source_input_id=q.source_input_id and p.status='대기' and p.kind in ('종류','칸','같은 칸','읽은 값')),'[]'))
 from public.proposals q where q.tenant_id=tid and q.id=qid;
$$;

create function public.passo_structure_preview(tid bigint,qid bigint)
returns jsonb language plpgsql security definer set search_path='' as $$
declare s jsonb;
begin
 if not public.passo_access(tid,'read') then raise insufficient_privilege; end if;
 s:=passo_private.structure_snapshot(tid,qid);
 if s is null or s->'proposal'->>'kind' not in ('종류','칸','같은 칸') then raise exception 'Missing structure review' using errcode='22023'; end if;
 if s->'proposal'->>'status'<>'대기' then raise exception 'Review changed' using errcode='PT409'; end if;
 return s||jsonb_build_object('revision',md5(s::text));
end $$;

-- Only the checked public mutation below may use these helpers.
create function passo_private.structure_alias(tid bigint,t text,label text,target text)
returns void language plpgsql set search_path='' as $$
begin
 if label is null or length(btrim(label)) not between 1 and 200 then raise exception 'Invalid label' using errcode='22023'; end if;
 insert into public.alias_dict(tenant_id,type_name,alias,prop_name) values(tid,t,label,target)
 on conflict(tenant_id,type_name,alias) do update set prop_name=excluded.prop_name;
end $$;

create function passo_private.structure_property(tid bigint,t text,p jsonb,sid bigint)
returns void language plpgsql set search_path='' as $$
declare old public.property_defs; pos integer; a text; ref text;
begin
 if jsonb_typeof(p) is distinct from 'object' or jsonb_typeof(p->'name') is distinct from 'string'
 or length(btrim(p->>'name')) not between 1 and 200 or jsonb_typeof(p->'label') is distinct from 'string'
 or length(btrim(p->>'label')) not between 1 and 200 or p->>'dtype' is null or p->>'dtype' not in ('number','text','date','choice')
 or (p ? 'is_entity' and jsonb_typeof(p->'is_entity')<>'boolean')
 or (p ? 'unit' and jsonb_typeof(p->'unit') not in ('string','null'))
 or (p ? 'ref_class' and jsonb_typeof(p->'ref_class') not in ('string','null')) then
  raise exception 'Invalid property' using errcode='22023';
 end if;
 select * into old from public.property_defs where tenant_id=tid and type_name=t and name=p->>'name';
 if found then
  if old.dtype is distinct from p->>'dtype' or old.unit is distinct from p->>'unit'
   or old.is_entity is distinct from coalesce((p->>'is_entity')::boolean,false) then
   raise exception 'Existing property differs' using errcode='PT409';
  end if;
 else
  select coalesce(max(position)+1,0) into pos from public.property_defs where tenant_id=tid and type_name=t;
  insert into public.property_defs(tenant_id,type_name,name,dtype,unit,is_entity,position)
   values(tid,t,p->>'name',p->>'dtype',p->>'unit',coalesce((p->>'is_entity')::boolean,false),pos);
 end if;
 foreach a in array array[p->>'label',p->>'name'] loop
  if exists(select 1 from public.alias_dict where tenant_id=tid and type_name=t and alias=a and prop_name is distinct from p->>'name') then
   raise exception 'Existing alias differs' using errcode='PT409';
  end if;
  perform passo_private.structure_alias(tid,t,a,p->>'name');
 end loop;
 -- Keep the follow-up entity classification as an explicit pending proposal.
 ref:=nullif(btrim(p->>'ref_class'),'');
 if ref is not null and not exists(select 1 from public.proposals where tenant_id=tid and kind='대상 분류' and payload->>'type'=t and payload->>'prop'=p->>'name') then
  if exists(select 1 from public.object_types where tenant_id=tid and name=ref and kind='기록') then ref:=ref||' (대상)'; end if;
  insert into public.proposals(tenant_id,kind,payload,evidence,source_input_id)
   values(tid,'대상 분류',jsonb_build_object('type',t,'prop',p->>'name','class',ref,'values',coalesce((
    select jsonb_agg(v) from (select distinct case when jsonb_typeof(r.data->(p->>'label'))='object' then r.data->(p->>'label')->>'value' else r.data->>(p->>'label') end v
     from public.records r where r.tenant_id=tid and r.type_name=t and (r.status='확정' or r.source_input_id=sid) limit 50) vals where v is not null),'[]')),
    '{"user":false}',sid);
 end if;
end $$;

create function passo_private.structure_type(tid bigint,q public.proposals,answer text)
returns void language plpgsql set search_path='' as $$
declare t text:=q.payload->>'type'; p jsonb; l jsonb; a text; mapped jsonb:='{}';
begin
  if jsonb_typeof(q.payload->'props') is distinct from 'array' or jsonb_array_length(q.payload->'props') not between 1 and 100 then raise exception 'Invalid properties' using errcode='22023'; end if;
  for p in select * from jsonb_array_elements(q.payload->'props') loop
   -- A single batch must not silently map one source label to two different columns.
   foreach a in array array[p->>'label',p->>'name'] loop
    if a is null or p->>'name' is null then raise exception 'Invalid label' using errcode='22023'; end if;
    if mapped ? a and mapped->>a is distinct from p->>'name' then raise exception 'Conflicting labels' using errcode='22023'; end if;
    mapped:=mapped||jsonb_build_object(a,p->>'name');
   end loop;
   if answer='split' then
    if jsonb_typeof(p->'name') is distinct from 'string' or length(btrim(p->>'name')) not between 1 and 200
     or jsonb_typeof(p->'label') is distinct from 'string' or length(btrim(p->>'label')) not between 1 and 200
     or p->>'dtype' is null or p->>'dtype' not in ('number','text','date','choice') then raise exception 'Invalid property' using errcode='22023'; end if;
    if not exists(select 1 from public.proposals where tenant_id=tid and kind='칸' and status='대기' and source_input_id is not distinct from q.source_input_id and payload=(p||jsonb_build_object('type',t))) then
     insert into public.proposals(tenant_id,kind,payload,evidence,source_input_id,created_by)
      values(tid,'칸',p||jsonb_build_object('type',t),jsonb_build_object('samples',q.evidence->'samples'->(p->>'label')),q.source_input_id,passo_private.current_user_id());
    end if;
   else perform passo_private.structure_property(tid,t,p,q.source_input_id);
   end if;
  end loop;
  if answer='accept' then
   if q.payload ? 'links' and jsonb_typeof(q.payload->'links')<>'array' then raise exception 'Invalid links' using errcode='22023'; end if;
   for l in select * from jsonb_array_elements(coalesce(q.payload->'links','[]')) loop
    if l->>'kind' is null or l->>'kind' not in ('연결','함께 움직임','최소 기준','최대 기준','의존','영향','대체')
     or l->>'from'=l->>'to' or not exists(select 1 from public.property_defs where tenant_id=tid and type_name=t and name=l->>'from')
     or not exists(select 1 from public.property_defs where tenant_id=tid and type_name=t and name=l->>'to') then raise exception 'Invalid link' using errcode='22023'; end if;
    if not exists(select 1 from public.proposals where tenant_id=tid and kind='연결' and status='대기' and payload->>'from'=t||'.'||(l->>'from') and payload->>'to'=t||'.'||(l->>'to') and payload->>'kind'=l->>'kind')
     and not exists(select 1 from public.link_types where tenant_id=tid and from_ref=t||'.'||(l->>'from') and to_ref=t||'.'||(l->>'to') and kind=l->>'kind') then
     insert into public.proposals(tenant_id,kind,payload,evidence,source_input_id)
      values(tid,'연결',l||jsonb_build_object('from',t||'.'||(l->>'from'),'to',t||'.'||(l->>'to')),'{"user":false}',q.source_input_id);
    end if;
   end loop;
  end if;

end $$;

create function passo_private.lock_structure(tid bigint,qid bigint,expected_revision text)
returns public.proposals language plpgsql set search_path='' as $$
declare q public.proposals; s jsonb;
begin
 -- Serialize structure decisions for a workspace; lock source and records before CAS.
 perform 1 from public.tenants where id=tid for update;
 select * into q from public.proposals where id=qid and tenant_id=tid for update;
 if not found or q.kind not in ('종류','칸','같은 칸') then raise exception 'Missing structure review' using errcode='22023'; end if;
 perform 1 from public.source_inputs where id=q.source_input_id and tenant_id=tid for update;
 perform 1 from public.records where source_input_id=q.source_input_id and tenant_id=tid order by id for update;
 perform 1 from public.object_types where tenant_id=tid and name=q.payload->>'type' for update;
 perform 1 from public.property_defs where tenant_id=tid and type_name=q.payload->>'type' order by id for update;
 perform 1 from public.alias_dict where tenant_id=tid and type_name=q.payload->>'type' order by id for update;
 s:=passo_private.structure_snapshot(tid,qid);
 if q.status<>'대기' or expected_revision is distinct from md5(s::text) then raise exception 'Review context changed' using errcode='PT409'; end if;
 return q;
end $$;

create function public.passo_answer_structure(tid bigint,qid bigint,expected_revision text,answer text)
returns public.proposals language plpgsql security definer set search_path='' as $$
declare q public.proposals; t text; target text; result_status text;
begin
 if not public.passo_access(tid,'review') then raise insufficient_privilege; end if;
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

revoke all on function passo_private.structure_snapshot(bigint,bigint),passo_private.structure_alias(bigint,text,text,text),passo_private.structure_property(bigint,text,jsonb,bigint),passo_private.structure_type(bigint,public.proposals,text),passo_private.lock_structure(bigint,bigint,text) from public,anon,authenticated;
revoke all on function public.passo_structure_preview(bigint,bigint),public.passo_answer_structure(bigint,bigint,text,text) from public,anon;
grant execute on function public.passo_structure_preview(bigint,bigint),public.passo_answer_structure(bigint,bigint,text,text) to authenticated;
commit;
