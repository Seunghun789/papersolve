-- Evaluate the authenticated identity once per statement, not for every data row.
begin;
create function public.passo_tenant_ids() returns setof bigint
language sql stable security definer set search_path = '' as $$
 with identity as materialized (select passo_private.current_user_id() as id)
 select m.tenant_id from public.memberships m join identity i on i.id=m.user_id
 union
 select t.id from public.tenants t join public.org_members m on m.org_id=t.org_id
 join identity i on i.id=m.user_id where m.role='주인'
$$;
revoke all on function public.passo_tenant_ids() from public,anon;
grant execute on function public.passo_tenant_ids() to authenticated;
do $$ declare t text; begin
 foreach t in array array['object_types','property_defs','alias_dict','link_types','entities',
 'entity_aliases','entity_links','source_inputs','records','proposals','widgets','automations','passo_jobs'] loop
 execute format('alter policy passo_read on public.%I using (tenant_id in (select public.passo_tenant_ids()))',t);
 end loop;
end $$;
alter policy passo_read on public.tenants using (id in (select public.passo_tenant_ids()));
commit;
