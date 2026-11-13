"""Opt-in live authentication/RLS/API/Storage probe. Creates and removes ONLY its own test entities."""
import argparse
import base64
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time
import uuid
import httpx

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--live',action='store_true',required=True);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--web-url');a=ap.parse_args()
 if a.output.exists():raise RuntimeError('Use a new report path')
 env=dict(l.split('=',1) for l in (ROOT/'.env').read_text().splitlines() if '=' in l and not l.startswith('#'))
 url=env['SUPABASE_URL'].strip();secret=env['SUPABASE_SECRET_KEY'].strip();pub=env['SUPABASE_KEY'].strip()
 os.environ['SUPABASE_URL']=url;os.environ['SUPABASE_PUBLISHABLE_KEY']=pub;os.environ['SUPABASE_STORAGE_SECRET_KEY']=secret
 admin={'apikey':secret,'Authorization':'Bearer '+secret,'Prefer':'return=representation'}
 anon={'apikey':pub}
 run='passo-auth-probe-'+uuid.uuid4().hex
 report={'status':'running','started_at':datetime.now(timezone.utc).isoformat(),'run':run,'project':url,'checks':[],
         'cleanup':{'verified':False},'limitations':['No real customer identity migration','Expired signed JWT tested locally; live test uses revocation and signature tampering','Worker execution not implemented']}
 created={'auth':[],'users':[],'orgs':[],'tenants':[],'objects':[]}
 c=httpx.Client(timeout=30,trust_env=False)
 def save():
  a.output.parent.mkdir(parents=True,exist_ok=True)
  tmp=a.output.with_suffix('.tmp');tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');tmp.replace(a.output)
 def check(name,condition,**details):
  report['checks'].append({'name':name,'passed':bool(condition),**details});save()
  if not condition:raise AssertionError(name)
 def request(method,path,headers=admin,**kw):
  started=time.monotonic()
  try:
   r=c.request(method,url+path,headers=headers,**kw)
   report.setdefault('http_metrics',[]).append({'method':method,'path':path.split('?')[0],'status':r.status_code,'seconds':round(time.monotonic()-started,3)})
   return r
  except httpx.RequestError as e:
   report['transport_error']={'method':method,'path':path.split('?')[0],'type':type(e).__name__};save();raise
 def ok(r):
  if not r.is_success:
   error=r.json() if r.headers.get('content-type','').startswith('application/json') else {}
   report['last_http_error']={'status':r.status_code,'path':r.request.url.path,'code':error.get('code'),'message':error.get('message')};save()
   raise RuntimeError('HTTP '+str(r.status_code)+' '+r.request.url.path)
  return r.json() if r.content else None
 def post(table,body):return ok(request('POST','/rest/v1/'+table,json=body))[0]
 def get(table,headers=admin,**params):return request('GET','/rest/v1/'+table,headers,params=params)
 def rpc(name,body,headers):return request('POST','/rest/v1/rpc/'+name,headers,json=body)
 def count(table):
  r=request('HEAD','/rest/v1/'+table,headers={**admin,'Prefer':'count=exact'},params={'select':'id'})
  if not r.is_success:raise RuntimeError('count failed')
  return int(r.headers['content-range'].split('/')[-1])
 def cleanup():
  failures=[]
  # Storage deletion before Auth users (Supabase owns storage rows by Auth ID).
  if created['objects']:
   r=request('DELETE','/storage/v1/object/passo-originals',json={'prefixes':created['objects']})
   if not r.is_success:failures.append('objects_delete')
  for table,key in [('tenants','tenants'),('orgs','orgs'),('users','users')]:
   for ident in created[key]:
    params={'id':f'eq.{ident}'}
    if table=='tenants':params['name']='like.'+run+'%'
    if table=='users':params['sub']='like.'+run+'%'
    r=request('DELETE','/rest/v1/'+table,params=params)
    if not r.is_success:failures.append(table+':'+str(ident))
  for ident in created['auth']:
   r=request('DELETE','/auth/v1/admin/users/'+ident)
   if not r.is_success:failures.append('auth:'+ident)
  for table,key in [('tenants','tenants'),('orgs','orgs'),('users','users')]:
   for ident in created[key]:
    if ok(get(table,id=f'eq.{ident}',select='id')):failures.append('remaining:'+table)
  for ident in created['auth']:
   if request('GET','/auth/v1/admin/users/'+ident).status_code!=404:failures.append('remaining:auth')
  for path in created['objects']:
   r=request('POST','/storage/v1/object/list/passo-originals',json={'prefix':path.rsplit('/',1)[0],'limit':100})
   if not r.is_success or r.json():failures.append('remaining:storage')
  report['after_counts']={t:count(t) for t in ('records','tenants','users')}
  report['cleanup']={'verified':not failures,'failures':failures,'created_ids':created}
  save()
 try:
  report['before_counts']={t:count(t) for t in ('records','tenants','users')};save()
  accounts=[]
  for n in range(3):
   email=run+str(n)+'@example.com';password=secrets.token_urlsafe(32)
   user=ok(request('POST','/auth/v1/admin/users',json={'email':email,'password':password,'email_confirm':True}))
   uid=user['id'];created['auth'].append(uid);save()
   legacy=post('users',{'provider':'email','sub':email,'name':run+str(n)})
   created['users'].append(legacy['id']);save()
   post('auth_identity_links',{'auth_user_id':uid,'legacy_user_id':legacy['id']})
   auth=ok(request('POST','/auth/v1/token?grant_type=password',anon,json={'email':email,'password':password}))
   headers={'apikey':pub,'Authorization':'Bearer '+auth['access_token']}
   accounts.append({'uid':uid,'legacy':legacy['id'],'headers':headers,'token':auth['access_token'],'refresh':auth['refresh_token'],'email':email,'password':password})
  for n in range(2):
   org=post('orgs',{'name':run+str(n)});created['orgs'].append(org['id']);save()
   t=post('tenants',{'name':run+str(n),'org_id':org['id']});created['tenants'].append(t['id']);save()
   post('memberships',{'tenant_id':t['id'],'user_id':accounts[n]['legacy'],'role':'주인'})
  ta,tb=created['tenants'];A,B,V=[x['headers'] for x in accounts]
  post('memberships',{'tenant_id':ta,'user_id':accounts[2]['legacy'],'role':'보는 사람'})
  sa=post('source_inputs',{'tenant_id':ta,'channel':'사진','name':run})
  sb=post('source_inputs',{'tenant_id':tb,'channel':'사진','name':run})
  records=[]
  for tid,source in [(ta,sa),(tb,sb)]:
   records.append(post('records',{'tenant_id':tid,'type_name':run,'occurred_at':'2026-11-13','source_input_id':source['id'],'data':{'quantity':'-','other':0}}))
  ra,rb=records
  ja=post('passo_jobs',{'tenant_id':ta,'source_input_id':sa['id']})
  jb=post('passo_jobs',{'tenant_id':tb,'source_input_id':sb['id']})
  post('passo_checkpoints',{'tenant_id':ta,'job_id':ja['id'],'revision':1,'state':{'private':'test'}})
  if a.web_url:
   from playwright.sync_api import sync_playwright
   with sync_playwright() as pw:
    browser=pw.chromium.launch()
    context=browser.new_context(viewport={'width':1200,'height':900})
    page=context.new_page()
    page.goto(a.web_url+'/login')
    page.locator('input[name=email]').fill(accounts[0]['email'])
    page.locator('input[name=password]').fill(accounts[0]['password'])
    page.get_by_role('button',name='이메일로 로그인').click()
    page.get_by_role('heading',name='내 기록',exact=True).wait_for(timeout=30000)
    check('Next.js real email login displays own records',page.locator('details.record').count()==1 and str(ra['id']) in page.locator('details.record').inner_text())
    cookies=[x for x in context.cookies() if x['name'].startswith('sb-')]
    check('session cookies HttpOnly and SameSite lax',bool(cookies) and all(x['httpOnly'] and x['sameSite']=='Lax' for x in cookies))
    check('browser JavaScript cannot read session cookies','sb-' not in page.evaluate('document.cookie'))
    page.locator('details.record summary').click()
    page.screenshot(path=str(a.output.with_suffix('.png')),full_page=True)
    page.goto(a.web_url+'/?workspace='+str(tb))
    check('Next.js foreign workspace URL reveals no records',page.locator('details.record').count()==0 and '접근할 수 없습니다' in page.inner_text('body'))
    page.get_by_role('button',name='로그아웃',exact=True).click()
    page.wait_for_url('**/login',timeout=30000)
    page.goto(a.web_url+'/')
    page.wait_for_url('**/login',timeout=30000)
    check('Next.js logout redirects protected page to login',not [x for x in context.cookies() if x['name'].startswith('sb-')])
    browser.close()
  check('A token validates with Supabase Auth',ok(request('GET','/auth/v1/user',A))['id']==accounts[0]['uid'])
  check('live session RPC',ok(rpc('passo_session_active',{},A)) is True)
  check('A sees only A records',[x['id'] for x in ok(get('records',A,select='id'))]==[ra['id']])
  check('B sees only B records',[x['id'] for x in ok(get('records',B,select='id'))]==[rb['id']])
  check('A cannot target B record',ok(get('records',A,id=f"eq.{rb['id']}"))==[])
  check('A cannot target B source',ok(get('source_inputs',A,id=f"eq.{sb['id']}"))==[])
  check('A cannot target B job',ok(get('passo_jobs',A,id=f"eq.{jb['id']}"))==[])
  check('checkpoints inaccessible to end user',get('passo_checkpoints',A).status_code in (401,403))
  for table in ['users','memberships','org_members','auth_identity_links','ai_logs','invites']:
   check('private table denied: '+table,get(table,A).status_code in (401,403))
  check('anonymous records denied',get('records',anon).status_code in (401,403))
  body={'tid':ta,'rid':ra['id'],'expected_data':ra['data'],'changes':{'quantity':0}}
  check('viewer edit denied',rpc('passo_edit_cells',body,V).status_code==403)
  check('cross-tenant edit denied',rpc('passo_edit_cells',{**body,'tid':tb,'rid':rb['id']},A).status_code==403)
  changed=ok(rpc('passo_edit_cells',body,A))[0]
  check('authorized dash to zero preserves other cell',changed['data']['quantity']['value']==0 and changed['data']['quantity']['status']=='value' and changed['data']['other']==0)
  check('stale edit returns conflict',rpc('passo_edit_cells',body,A).status_code==409)
  check('direct data patch blocked',request('PATCH','/rest/v1/records',A,params={'id':f"eq.{ra['id']}"},json={'data':{}}).status_code==403)
  patha=f'{ta}/{uuid.uuid4()}/probe.png';pathb=f'{tb}/{uuid.uuid4()}/probe.png'
  png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jN1sAAAAASUVORK5CYII=')
  for path,headers in [(patha,A),(pathb,B)]:
   r=request('POST','/storage/v1/object/passo-originals/'+path,{**headers,'Content-Type':'image/png'},content=png)
   ok(r);created['objects'].append(path);save()
  own=request('GET','/storage/v1/object/authenticated/passo-originals/'+patha,A)
  check('direct owner download denied: API required',not own.is_success,status=own.status_code)
  r=request('GET','/storage/v1/object/authenticated/passo-originals/'+pathb,A)
  check('other organization original denied',r.status_code in (400,401,403,404),status=r.status_code)
  r=request('POST','/storage/v1/object/passo-originals/'+f'{tb}/{uuid.uuid4()}/forbidden.png',{**A,'Content-Type':'image/png'},content=png)
  check('cross-tenant original upload denied',not r.is_success,status=r.status_code)
  check('original overwrite denied',not request('PUT','/storage/v1/object/passo-originals/'+patha,{**A,'Content-Type':'image/png'},content=png).is_success)
  from fastapi.testclient import TestClient
  from backend.main import app
  with TestClient(app) as api:
   apiA={'Authorization':A['Authorization']}
   r=api.get(f'/v1/workspaces/{ta}/records',headers=apiA)
   check('FastAPI with real token returns only own records',r.status_code==200 and [x['id'] for x in r.json()['items']]==[ra['id']])
   check('FastAPI blocks other tenant',api.get(f'/v1/workspaces/{tb}/records',headers=apiA).status_code==403)
   check('FastAPI blocks other tenant job',api.get(f'/v1/workspaces/{tb}/jobs/'+jb['id'],headers=apiA).status_code==403)
   r=api.get(f'/v1/workspaces/{ta}/originals/'+patha.split('/',1)[1],headers=apiA)
   check('FastAPI serves authorized original',r.status_code==200 and r.content==png)
   ok(request('PATCH','/rest/v1/memberships',params={'tenant_id':f'eq.{ta}','user_id':f"eq.{accounts[0]['legacy']}"},json={'role':'보는 사람'}))
   check('same token role demotion blocks API edit',api.patch(f"/v1/workspaces/{ta}/records/{ra['id']}/cells",headers=apiA,json={'expected_data':changed['data'],'changes':{'quantity':3}}).status_code==403)
   ok(request('DELETE','/rest/v1/memberships',params={'tenant_id':f'eq.{ta}','user_id':f"eq.{accounts[2]['legacy']}"}))
   check('membership removal denies old token',ok(get('records',V,select='id'))==[])
   refreshed=ok(request('POST','/auth/v1/token?grant_type=refresh_token',anon,json={'refresh_token':accounts[1]['refresh']}))
   Bfresh={'apikey':pub,'Authorization':'Bearer '+refreshed['access_token']}
   check('refreshed session retains correct tenant',ok(rpc('passo_access',{'tid':tb},Bfresh)) is True and ok(rpc('passo_access',{'tid':ta},Bfresh)) is False)
   r=request('POST','/auth/v1/logout?scope=local',A);check('Supabase logout succeeds',r.is_success,status=r.status_code)
   check('logged out access token cannot read records',ok(get('records',A,select='id'))==[])
   r=api.get('/v1/workspaces',headers=apiA)
   check('logged out token blocked by FastAPI',r.status_code==401,status=r.status_code)
   check('logged out token cannot download original directly',not request('GET','/storage/v1/object/authenticated/passo-originals/'+patha,A).is_success)
   check('logged out token cannot download original via API',api.get(f'/v1/workspaces/{ta}/originals/'+patha.split('/',1)[1],headers=apiA).status_code==401)
   parts=accounts[1]['token'].split('.');parts[2]='a'+parts[2][1:]
   check('tampered signature rejected',api.get('/v1/workspaces',headers={'Authorization':'Bearer '+'.'.join(parts)}).status_code==401)
  report['status']='passed'
 except Exception as exc:
  report['status']='failed';report['error_type']=type(exc).__name__
  # No transport exception, secrets or bodies serialized.
  save();raise
 finally:
  try:cleanup()
  finally:
   report['finished_at']=datetime.now(timezone.utc).isoformat();save();c.close()
 print(json.dumps({'status':report['status'],'checks':len(report['checks']),'cleanup':report['cleanup']['verified'],'before':report['before_counts'],'after':report['after_counts']}))
 if not report['cleanup']['verified']:raise SystemExit(1)

if __name__=='__main__':main()
