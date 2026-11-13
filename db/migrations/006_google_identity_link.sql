-- Match a verified Google identity to its immutable legacy provider subject.
-- No email matching, membership creation, or changes to existing links.
begin;
create or replace function public.passo_link_google_identity() returns boolean
language plpgsql security definer set search_path = '' as $$
declare aid uuid := auth.uid(); lid bigint;
begin
  if not exists (
    select 1 from auth.users u join auth.sessions s on s.user_id=u.id
    where u.id=aid and s.id::text=auth.jwt()->>'session_id'
      and (s.not_after is null or s.not_after>now())
      and (u.banned_until is null or u.banned_until<now())
      and coalesce((auth.jwt()->>'exp')::numeric,0)>extract(epoch from now())
  ) or not exists (
    select 1 from jsonb_array_elements(coalesce(auth.jwt()->'amr','[]')) a
    where a->>'method'='oauth'
  ) then raise insufficient_privilege; end if;
  select l.id into lid from public.users l
    join auth.identities i on i.provider='google' and i.user_id=aid
      and i.provider_id=l.sub
    where l.provider='google';
  if lid is null then return false; end if;
  -- Unique constraints serialize concurrent requests and reject conflicting links.
  insert into public.auth_identity_links(auth_user_id,legacy_user_id)
    values(aid,lid) on conflict do nothing;
  return exists(select 1 from public.auth_identity_links
    where auth_user_id=aid and legacy_user_id=lid);
end $$;
revoke all on function public.passo_link_google_identity() from public,anon;
grant execute on function public.passo_link_google_identity() to authenticated;
commit;
