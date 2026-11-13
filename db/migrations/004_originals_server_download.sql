-- Authenticated Storage CDN responses can outlive session revocation.
-- Only the API's storage adapter may read bytes after checking live user permissions.
begin;
drop policy passo_storage_read on storage.objects;
create policy passo_storage_server_read on storage.objects as restrictive for select to public
 using (bucket_id <> 'passo-originals');
commit;
