import { PGlite } from '../../apps/web/node_modules/@electric-sql/pglite/dist/index.js';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';
import assert from 'node:assert/strict';
const db=new PGlite();
const checks=[];
const check=(name, condition)=>{assert.ok(condition,name);checks.push({name,passed:true});};
const uid=n=>`00000000-0000-4000-8000-${String(n).padStart(12,'0')}`;
const sessionId=n=>uid(n+100);
async function as(n,sql,params=[],role='authenticated',overrides={}) {
 await db.exec('reset role');
 await db.query("select set_config('request.jwt.claims',$1,false)",[JSON.stringify({sub:uid(n),session_id:sessionId(n),exp:Math.floor(Date.now()/1000)+3600,...overrides})]);
 await db.exec(`set role ${role}`);
 try {return (await db.query(sql,params)).rows;} finally {await db.exec('reset role');}
}
async function denied(name,fn) {
 try {await fn();} catch(e) {if(['42501','23503','22023','40001','PT409'].includes(e.code)){check(name,true);return;}throw e;}
 throw new Error(name+' unexpectedly allowed');
}
try {
 await db.exec(`create role anon; create role authenticated; create role service_role bypassrls; create schema auth; create schema storage;
 create table auth.users(id uuid primary key,banned_until timestamptz);
 create table auth.sessions(id uuid primary key,user_id uuid references auth.users(id),not_after timestamptz);
 create table auth.identities(user_id uuid references auth.users(id),provider text,provider_id text,identity_data jsonb);
 create function auth.jwt() returns jsonb language sql stable as $$select coalesce(nullif(current_setting('request.jwt.claims',true),''),'{}')::jsonb$$;
 create function auth.uid() returns uuid language sql stable as $$select (auth.jwt()->>'sub')::uuid$$;
 create table storage.buckets(id text primary key,name text,public boolean,file_size_limit bigint,allowed_mime_types text[]);
 create table storage.objects(id bigint generated always as identity primary key,bucket_id text references storage.buckets(id),name text);
 alter table storage.objects enable row level security;
 grant usage on schema auth,storage to anon,authenticated;
 grant all on storage.objects to authenticated;
 grant select on storage.objects to anon;
 grant usage on sequence storage.objects_id_seq to authenticated;
 create policy preexisting_broad on storage.objects for all to authenticated using(true) with check(true);
 create policy preexisting_anon on storage.objects for select to anon using(true);
 `);
 await db.exec(await readFile('db/schema.sql','utf8'));
 await db.exec(await readFile('db/migrations/001_auth_isolation.sql','utf8'));
 await db.exec(await readFile('db/migrations/002_conflict_http_status.sql','utf8'));
 await db.exec(await readFile('db/migrations/003_rls_membership_set.sql','utf8'));
 await db.exec(await readFile('db/migrations/004_originals_server_download.sql','utf8'));
 await db.exec(await readFile('db/migrations/005_document_pipeline.sql','utf8'));
 await db.exec(await readFile('db/migrations/006_google_identity_link.sql','utf8'));
 await db.exec(await readFile('db/migrations/007_workspace_ui.sql','utf8'));
 await db.exec(await readFile('db/migrations/008_structure_reviews.sql','utf8'));
 await db.exec(await readFile('db/migrations/009_policy_undo.sql','utf8'));
 check('migrations apply to original schema',true);
 for(let n=1;n<=5;n++) {
  await db.query('insert into auth.users(id) values($1)',[uid(n)]);
  await db.query('insert into auth.sessions(id,user_id) values($1,$2)',[sessionId(n),uid(n)]);
  await db.query("insert into public.users(provider,sub,is_master) values('email',$1,$2)",['test'+n,n===4]);
  await db.query('insert into public.auth_identity_links(auth_user_id,legacy_user_id) values($1,$2)',[uid(n),n]);
 }
 await db.exec(`insert into public.orgs(name) values('A'),('B');
 insert into public.tenants(name,org_id) values('A-one',1),('B-one',2),('A-two',1);
 insert into public.memberships(tenant_id,user_id,role) values(1,1,'주인'),(2,2,'주인'),(1,3,'보는 사람'),(1,5,'올리는 사람');
 insert into public.org_members(org_id,user_id,role) values(1,1,'주인');
 insert into public.source_inputs(tenant_id,channel) values(1,'사진'),(2,'사진');
 insert into public.records(tenant_id,type_name,occurred_at,data,source_input_id)
 values(1,'A','2026-11-13','{"quantity":"-","other":0}',1),(2,'B','2026-11-13','{"quantity":99}',2);
 insert into public.passo_jobs(id,tenant_id,source_input_id) values('${uid(201)}',1,1),('${uid(202)}',2,2);
 insert into public.passo_checkpoints(tenant_id,job_id,revision,state) values(1,'${uid(201)}',1,'{}'),(2,'${uid(202)}',1,'{}');
 insert into storage.objects(bucket_id,name) values('passo-originals','1/${uid(301)}/a.png'),('passo-originals','2/${uid(302)}/b.png');
 `);
 check('A sees only A records',(await as(1,'select id from public.records')).map(x=>x.id).join()==='1');
 check('B sees only B records',(await as(2,'select id from public.records')).map(x=>x.id).join()==='2');
 check('A cannot target B record ID',(await as(1,'select * from public.records where id=2')).length===0);
 check('A cannot target B source',(await as(1,'select * from public.source_inputs where id=2')).length===0);
 check('org owner inherits only its own workspaces',(await as(1,'select * from public.passo_workspaces()')).map(x=>x.id).join()==='1,3');
 check('legacy master flag grants no access',(await as(4,'select * from public.records')).length===0);
 check('A cannot read B jobs',(await as(1,'select id from public.passo_jobs')).map(x=>x.id).join()===uid(201));
 for (const table of ['users','org_members','memberships','invites','auth_identity_links','ai_logs','chat_sessions','eval_votes','passo_checkpoints']) {
  await denied(`authenticated cannot read private ${table}`,()=>as(1,`select * from public.${table}`));
 }
 for(const table of ['records','source_inputs','tenants','passo_jobs']) {
  await denied(`anonymous cannot read ${table}`,()=>as(1,`select * from public.${table}`,[],'anon'));
 }
 await denied('cannot self-promote',()=>as(3,"update public.memberships set role='주인' where user_id=3"));
 await denied('cannot insert identity link',()=>as(4,`delete from public.auth_identity_links`));
 await denied('cannot move record to another tenant',()=>as(1,'update public.records set tenant_id=2 where id=1'));
 await denied('cannot bypass cell RPC with direct data update',()=>as(1,"update public.records set data='{}' where id=1"));
 await denied('cannot create worker job',()=>as(1,"insert into public.passo_jobs(tenant_id) values(1)"));
 await denied('job source cannot reference another tenant',()=>db.exec(`insert into public.passo_jobs(tenant_id,source_input_id) values(1,2)`));
 await denied('checkpoint cannot reference another tenant job',()=>db.exec(`insert into public.passo_checkpoints(tenant_id,job_id,revision,state) values(1,'${uid(202)}',2,'{}')`));
 const expected={quantity:'-',other:0};
 const edit=(n,tid=1,rid=1,data=expected,changes={quantity:0})=>as(n,'select * from public.passo_edit_cells($1,$2,$3,$4)',[tid,rid,data,changes]);
 await denied('viewer cannot edit',()=>edit(3));
 await denied('uploader cannot review',()=>edit(5));
 await denied('A cannot edit B using B tenant',()=>edit(1,2,2,{quantity:99}));
 check('A cannot edit B using A tenant',(await edit(1,1,2)).length===0);
 const edited=(await edit(1))[0];
 check('dash to zero preserves raw and normalized state',edited.data.quantity.raw===0 && edited.data.quantity.value===0 && edited.data.quantity.status==='value');
 check('patch preserves other cells',edited.data.other===0);
 await denied('stale edit conflicts',()=>edit(1));
 await denied('arbitrary JSON cell rejected',()=>edit(1,1,1,edited.data,{quantity:{status:'value',value:999}}));
 await denied('new undeclared label rejected',()=>edit(1,1,1,edited.data,{injected:1}));
 let current=edited.data;
 for(const [value,status] of [[' - ','explicit_dash'],[' \t','blank'],[null,'unknown'],['0','value']]) {
  current=(await edit(1,1,1,current,{quantity:value}))[0].data;
  check('SQL cell normalization '+status,current.quantity.status===status && current.quantity.raw===value);
 }
 check('even owner cannot download through Storage directly',(await as(1,'select name from storage.objects')).length===0);
 try {check('anonymous sees no files',(await as(1,'select * from storage.objects',[],'anon')).length===0);} catch(e) {if(e.code!=='42501')throw e;check('anonymous file select denied',true);}
 await denied('A cannot upload into B path',()=>as(1,"insert into storage.objects(bucket_id,name) values('passo-originals',$1)",[`2/${uid(303)}/x.png`]));
 await denied('viewer cannot upload',()=>as(3,"insert into storage.objects(bucket_id,name) values('passo-originals',$1)",[`1/${uid(303)}/x.png`]));
 await as(5,"insert into storage.objects(bucket_id,name) values('passo-originals',$1)",[`1/${uid(303)}/x.png`]);
 check('uploader can create own original',true);
 check('preexisting broad policy cannot overwrite original',(await as(1,"update storage.objects set name='1/changed' returning id")).length===0);
 check('preexisting broad policy cannot delete original',(await as(1,'delete from storage.objects returning id')).length===0);
 for(const path of ['../2/a','1/../a','01/a/b','999999999999999999999999999999999999/00000000-0000-4000-8000-000000000000/a.png']) {
  check('malformed path denied '+path,(await as(1,"select public.passo_object_access($1,'read') as allowed",[path]))[0].allowed===false);
 }
 await (await import('../documents/pipeline-cases.mjs')).pipelineCases({db,as,denied,check,uid});
 await (await import('../documents/workspace-cases.mjs')).workspaceCases({db,as,denied,check,uid});
 await (await import('../documents/structure-cases.mjs')).structureCases({db,as,denied,check,uid});
 await (await import('../documents/policy-cases.mjs')).policyCases({db,as,denied,check,uid});
 await db.exec("update public.memberships set role='확인하는 사람' where user_id=3");
 check('role promotion visible on same token',(await as(3,"select public.passo_access(1,'review') as allowed"))[0].allowed===true);
 await db.exec("update public.memberships set role='보는 사람' where user_id=3");
 await denied('role demotion enforced on same token',()=>edit(3));
 await db.exec('delete from public.memberships where user_id=3');
 check('membership removal denies same token',(await as(3,'select * from public.records')).length===0);
 await db.exec('delete from public.org_members where user_id=1');
 check('org owner removal removes inherited workspace',(await as(1,'select * from public.passo_workspaces()')).map(x=>x.id).join()==='1');
 check('expired access token sees no records',(await as(1,'select * from public.records',[],'authenticated',{exp:1})).length===0);
 check('missing session claim denies records',(await as(1,'select * from public.records',[],'authenticated',{session_id:null})).length===0);
 check('another users session cannot be reused',(await as(1,'select * from public.records',[],'authenticated',{session_id:sessionId(2)})).length===0);
 await db.query('update auth.sessions set not_after=now()-interval \'1 second\' where id=$1',[sessionId(1)]);
 check('session timebox expires immediately',(await as(1,'select * from public.records')).length===0);
 await db.query('update auth.sessions set not_after=null where id=$1',[sessionId(1)]);
 await db.query("update auth.users set banned_until=now()+interval '1 hour' where id=$1",[uid(1)]);
 check('banned account denied even with active session',(await as(1,'select * from public.records')).length===0);
 await db.query('update auth.users set banned_until=null where id=$1',[uid(1)]);
 await db.query('delete from auth.sessions where id=$1',[sessionId(1)]);
 check('revoked session denies reads',(await as(1,'select * from public.records')).length===0);
 await denied('revoked session denies edits',()=>edit(1));
 check('revoked session denies files',(await as(1,'select * from storage.objects')).length===0);
 await db.query('delete from public.auth_identity_links where auth_user_id=$1',[uid(2)]);
 check('unlinked auth account denied',(await as(2,'select * from public.records')).length===0);
 // A matching email or user-editable metadata must never grant a legacy identity.
 await db.query("insert into public.users(provider,sub,email) values('google','google-subject','same@example.test') returning id");
 const legacyGoogle=(await db.query("select id from public.users where sub='google-subject'")).rows[0].id;
 await db.query("insert into auth.identities values($1,'google','different-subject','{\"email\":\"same@example.test\",\"sub\":\"google-subject\"}')",[uid(2)]);
 const oauth={amr:[{method:'oauth'}]};
 const link=(n=2,overrides=oauth,role='authenticated')=>as(n,'select public.passo_link_google_identity() as linked',[],role,overrides);
 check('same email and spoofed subject metadata do not link',(await link())[0].linked===false);
 await denied('anonymous cannot invoke linking',()=>link(2,oauth,'anon'));
 await denied('password session cannot claim Google identity',()=>link(2,{amr:[{method:'password'}]}));
 await denied('expired token cannot link',()=>link(2,{...oauth,exp:1}));
 await denied('another users session cannot link',()=>link(2,{...oauth,session_id:sessionId(3)}));
 await db.query("update auth.identities set provider_id='google-subject' where user_id=$1",[uid(2)]);
 await db.query("update auth.users set banned_until=now()+interval '1 hour' where id=$1",[uid(2)]);
 await denied('banned Google user cannot link',()=>link());
 await db.query('update auth.users set banned_until=null where id=$1',[uid(2)]);
 await db.query("update auth.sessions set not_after=now()-interval '1 second' where id=$1",[sessionId(2)]);
 await denied('expired session cannot link',()=>link());
 await db.query('update auth.sessions set not_after=null where id=$1',[sessionId(2)]);
 const membershipBefore=JSON.stringify((await db.query('select * from public.memberships order by tenant_id,user_id')).rows);
 check('verified immutable Google subject links',(await link())[0].linked===true);
 check('repeat callback is idempotent',(await link())[0].linked===true);
 check('link points to exact legacy ID',(await db.query('select legacy_user_id from public.auth_identity_links where auth_user_id=$1',[uid(2)])).rows[0].legacy_user_id===legacyGoogle);
 check('linking creates no workspace access',(await as(2,'select * from public.passo_workspaces()')).length===0);
 check('membership rows are unchanged',JSON.stringify((await db.query('select * from public.memberships order by tenant_id,user_id')).rows)===membershipBefore);
 await db.query("insert into auth.identities values($1,'google','google-subject','{}')",[uid(3)]);
 check('conflicting link never replaces existing identity',(await link(3))[0].linked===false);
 check('other legacy mapping preserved',(await db.query('select legacy_user_id from public.auth_identity_links where auth_user_id=$1',[uid(3)])).rows[0].legacy_user_id===3);
 await db.query('delete from auth.sessions where id=$1',[sessionId(2)]);
 await denied('revoked OAuth session cannot link',()=>link());
 console.log(JSON.stringify({status:'passed',count:checks.length}));
} catch(e) {
 console.error(e.message);process.exitCode=1;
 checks.push({name:'failure',passed:false,error:e.message});
} finally {
 const output=process.env.PASSO_RLS_REPORT || 'artifacts/auth-rls/rls.json';
 await mkdir(dirname(output),{recursive:true});
 await writeFile(output,JSON.stringify({
   environment:'PGlite PostgreSQL engine; local auth/storage schema fixtures, NOT live Supabase gateway or Storage HTTP',
   checks,passed:checks.every(x=>x.passed)},null,2));
 await db.close();
}
