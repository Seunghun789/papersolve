-- Business conflict must not trigger PostgREST serialization retries.
begin;
create or replace function public.passo_edit_cells(tid bigint, rid bigint, expected_data jsonb, changes jsonb)
returns setof public.records language plpgsql security definer set search_path = '' as $$
declare current_data jsonb; k text; v jsonb; normalized jsonb;
begin
  if not public.passo_access(tid, 'review') then raise insufficient_privilege; end if;
  select data into current_data from public.records where id=rid and tenant_id=tid for update;
  if not found then return; end if;
  if current_data is distinct from expected_data then raise exception 'Record changed' using errcode='PT409'; end if;
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

commit;
