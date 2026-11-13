"""Opt-in real Auth/Storage/HTTP/UI/model test, with scoped fixtures and verified cleanup."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import uuid
import httpx

ROOT=Path(__file__).resolve().parents[1]
DOCKER='/Applications/Docker.app/Contents/Resources/bin/docker'


def main():
 p=argparse.ArgumentParser();p.add_argument('--live',action='store_true',required=True)
 p.add_argument('--output',type=Path,required=True);p.add_argument('--api',default='http://localhost:8001')
 p.add_argument('--web',default='http://localhost:3001');p.add_argument('--crash',action='store_true')
 a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
 v=dict(l.split('=',1) for l in (ROOT/'.env').read_text().splitlines() if '=' in l and not l.startswith('#'))
 url=v['SUPABASE_URL'].strip();secret=v['SUPABASE_SECRET_KEY'].strip();pub=v['SUPABASE_KEY'].strip()
 admin={'apikey':secret,'Authorization':'Bearer '+secret,'Prefer':'return=representation'}
 c=httpx.Client(timeout=45,trust_env=False);run='passo-doc-probe-'+uuid.uuid4().hex
 created={'auth':[],'users':[],'tenants':[],'objects':[]};processes=[]
 report={'status':'running','run':run,'api':a.api,'web':a.web,'checks':[],'cleanup':False,'started_at':datetime.now(timezone.utc).isoformat()}
 def save():
  temp=a.output/'report.tmp';temp.write_text(json.dumps(report,ensure_ascii=False,indent=2));temp.replace(a.output/'report.json')
 def check(name,condition,**details):
  report['checks'].append({'name':name,'passed':bool(condition),**details});save()
  if not condition:raise AssertionError(name)
 def sb(method,path,**kw):
  for attempt in range(3):
   try:return c.request(method,url+path,headers=admin,**kw)
   except httpx.TransportError:
    if method not in ('GET','HEAD') or attempt==2:raise
    time.sleep(1)
 def ok(r):
  if not r.is_success:
   report['last_error']={'path':r.request.url.path,'status':r.status_code};save();raise RuntimeError('HTTP '+str(r.status_code))
  return r.json() if r.content else None
 def post(table,data):return ok(sb('POST','/rest/v1/'+table,json=data))[0]
 def count(table):
  r=c.head(url+'/rest/v1/'+table,headers={**admin,'Prefer':'count=exact'},params={'select':'id'});r.raise_for_status()
  return int(r.headers['content-range'].split('/')[-1])
 def wait_for(fn,timeout=240):
  end=time.monotonic()+timeout
  while time.monotonic()<end:
   result=fn()
   if result:return result
   time.sleep(2)
  raise TimeoutError('probe wait')
 def cleanup():
  for proc in processes:
   if proc.poll() is None:proc.kill();proc.wait(timeout=20)
  failures=[]
  # Discover this probe's originals even if upload acknowledgement failed.
  for tid in created['tenants']:
   rows=ok(sb('GET','/rest/v1/source_inputs',params={'tenant_id':f'eq.{tid}','select':'storage_path'}))
   created['objects']+= [r['storage_path'] for r in rows if r.get('storage_path')]
  created['objects']=list(set(created['objects']))
  if created['objects']:
   if not sb('DELETE','/storage/v1/object/passo-originals',json={'prefixes':created['objects']}).is_success:failures.append('storage_delete')
  for table in ['tenants','users']:
   for ident in created[table]:
    if not sb('DELETE','/rest/v1/'+table,params={'id':f'eq.{ident}'}).is_success:failures.append(table)
    if ok(sb('GET','/rest/v1/'+table,params={'id':f'eq.{ident}','select':'id'})):failures.append('remaining_'+table)
  for ident in created['auth']:
   if not sb('DELETE','/auth/v1/admin/users/'+ident).is_success:failures.append('auth_delete')
   if sb('GET','/auth/v1/admin/users/'+ident).status_code!=404:failures.append('remaining_auth')
  for path in created['objects']:
   if ok(sb('POST','/storage/v1/object/list/passo-originals',json={'prefix':path.rsplit('/',1)[0],'limit':100})):failures.append('remaining_object')
  report['after_counts']={t:count(t) for t in ('records','tenants','users')}
  report['cleanup']={'verified':not failures,'failures':failures,'created':created};save()
 try:
  report['before_counts']={t:count(t) for t in ('records','tenants','users')};save();accounts=[]
  for n in range(3):
   email=run+str(n)+'@example.com';password=secrets.token_urlsafe(32)
   au=ok(sb('POST','/auth/v1/admin/users',json={'email':email,'password':password,'email_confirm':True}));created['auth'].append(au['id']);save()
   u=post('users',{'provider':'email','sub':email,'name':run});created['users'].append(u['id']);save()
   post('auth_identity_links',{'auth_user_id':au['id'],'legacy_user_id':u['id']})
   token=ok(c.post(url+'/auth/v1/token?grant_type=password',headers={'apikey':pub},json={'email':email,'password':password}))['access_token']
   accounts.append({'id':u['id'],'email':email,'password':password,'Authorization':'Bearer '+token})
  for n in range(2):
   t=post('tenants',{'name':run+str(n),'title':'문서 흐름 검증'});created['tenants'].append(t['id']);save()
   post('memberships',{'tenant_id':t['id'],'user_id':accounts[n]['id'],'role':'주인'})
  tid=created['tenants'][0];post('memberships',{'tenant_id':tid,'user_id':accounts[2]['id'],'role':'보는 사람'})
  apiheaders={'Authorization':accounts[0]['Authorization']};base=f'{a.api}/v1/workspaces/{tid}/documents'
  fixture=ROOT/'artifacts/auth-policy-20261113/policy/printed-1.png'
  content=fixture.read_bytes();report['fixture']={'path':str(fixture),'sha256':hashlib.sha256(content).hexdigest()};save()
  from playwright.sync_api import sync_playwright
  with sync_playwright() as pw:
   browser=pw.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1050})
   page.goto(a.web+'/login');page.locator('input[name=email]').fill(accounts[0]['email']);page.locator('input[name=password]').fill(accounts[0]['password'])
   page.get_by_role('button',name='이메일로 로그인').click();page.get_by_role('heading',name='문서 처리',exact=True).wait_for(timeout=30000)
   page.get_by_label('문서 파일').set_input_files(str(fixture))
   with page.expect_response(lambda r:'/api/workspaces/' in r.url and r.request.method=='POST',timeout=45000) as response:
    page.get_by_role('button',name='문서 올리기',exact=True).click()
   r=response.value;check('browser upload reaches real API',r.status==202,status=r.status)
   j=ok(c.get(base,headers=apiheaders))[0];jid=j['id'];report['job']=jid;save()
   key=r.request.headers['idempotency-key']
   again=ok(c.post(base,headers={**apiheaders,'Content-Type':'image/png','Idempotency-Key':key},params={'filename':fixture.name},content=content))
   check('same upload retry returns one job',again['id']==jid)
   check('idempotency key cannot be reused for another file',c.post(base,headers={**apiheaders,'Content-Type':'image/png','Idempotency-Key':key},params={'filename':fixture.name},content=content+b'\n').status_code==409)
   check('foreign organization cannot read document',c.get(base+'/'+jid,headers={'Authorization':accounts[1]['Authorization']}).status_code==403)
   check('viewer cannot upload',c.post(base,headers={'Authorization':accounts[2]['Authorization'],'Content-Type':'image/png','Idempotency-Key':str(uuid.uuid4())},content=content).status_code==403)
   if a.crash:
    env={**os.environ,'SUPABASE_URL':url,'SUPABASE_WORKER_SECRET_KEY':secret,'ANTHROPIC_API_KEY':v['ANTHROPIC_API_KEY'].strip()}
    # Test-only barrier immediately before the real provider call. Production worker is unchanged.
    script="import time; import backend.extraction as e; from backend.worker import Worker; e.run_model=lambda *a:time.sleep(600); w=Worker(); w.process(w.rpc('passo_claim_document')[0])"
    log=(a.output/'interrupted-worker.log').open('w')
    proc=subprocess.Popen([sys.executable,'-c',script],cwd=ROOT,env=env,stdout=log,stderr=log);processes.append(proc)
    checkpoint=wait_for(lambda:ok(sb('GET','/rest/v1/passo_checkpoints',params={'job_id':f'eq.{jid}','select':'revision,state'})))
    check('real OCR checkpoint persisted before crash','ocr' in checkpoint[0]['state'] and 'extraction' not in checkpoint[0]['state'])
    proc.kill();proc.wait(timeout=20);log.close()
    check('worker forcibly terminated',proc.returncode<0)
    # Wait for the unchanged production lease TTL. A replacement Docker worker reclaims it.
    subprocess.run([DOCKER,'compose','up','-d','worker'],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
   def ready():
    d=ok(c.get(base+'/'+jid,headers=apiheaders))
    if d['job']['status']=='failed':raise RuntimeError('worker failed')
    return d if d['job']['status']=='awaiting_review' else None
   d=wait_for(ready,360)
   check('OCR and real model publish reviewable rows',len(d['records'])==6,rows=len(d['records']))
   if a.crash:check('expired job recovered by new worker',d['job']['attempts']==2,attempts=d['job']['attempts'])
   cells=[c for r in d['records'] for k,c in r['data'].items() if k in ('입고','출고','재고')]
   gold=json.loads((fixture.parent/'fixtures.json').read_text())['documents'][0]['cells']
   by_row={str(r['data']['번호']['raw']):r['data'] for r in d['records']}
   matched=sum(by_row.get(g['row'],{}).get(g['label'],{}).get('raw')==g['raw'] and
               by_row.get(g['row'],{}).get(g['label'],{}).get('status')==g['status'] for g in gold)
   check('zero dash blank preserved in every expected cell',len(cells)==18 and matched==len(gold),correct=matched,total=len(gold))
   original=c.get(base+'/'+jid+'/original',headers=apiheaders)
   check('retrieved original bytes match upload',original.status_code==200 and hashlib.sha256(original.content).hexdigest()==report['fixture']['sha256'])
   check('foreign original download denied',c.get(base+'/'+jid+'/original',headers={'Authorization':accounts[1]['Authorization']}).status_code==403)
   check('viewer confirmation denied',c.post(base+'/'+jid+'/confirm',headers={'Authorization':accounts[2]['Authorization']}).status_code==403)
   page.get_by_role('button',name='내용 확인 후 확정',exact=True).wait_for(timeout=30000)
   page.locator('.evidence-cell').first.click();page.locator('.evidence-image img').wait_for()
   page.wait_for_function("document.querySelector('.evidence-image img')?.naturalWidth > 0")
   check('UI opens source evidence image',page.locator('.evidence-image img').count()==1)
   page.screenshot(path=str(a.output/'evidence.png'),full_page=True)
   page.get_by_role('button',name='내용 확인 후 확정',exact=True).click()
   wait_for(lambda:ok(c.get(base+'/'+jid,headers=apiheaders))['job']['status']=='succeeded',30)
   ok(c.post(base+'/'+jid+'/confirm',headers=apiheaders));final=ok(c.get(base+'/'+jid,headers=apiheaders))
   check('confirmation retry creates no duplicate records',len(final['records'])==6 and all(r['status']=='확정' for r in final['records']))
   checkpoint=ok(sb('GET','/rest/v1/passo_checkpoints',params={'job_id':f'eq.{jid}','order':'revision.desc','limit':1,'select':'state'}))[0]['state']
   report['usage']=checkpoint['extraction']['usage'];report['ocr_lines']=sum(len(x) for x in checkpoint['ocr'].values())
   (a.output/'result.json').write_text(json.dumps(final,ensure_ascii=False,indent=2));save()
   page.get_by_role('button',name='로그아웃',exact=True).click();page.wait_for_url('**/login')
   ok(c.post(url+'/auth/v1/logout?scope=local',headers={'apikey':pub,**apiheaders}))
   check('revoked session cannot fetch original',c.get(base+'/'+jid+'/original',headers=apiheaders).status_code==401)
   browser.close()
  report['status']='passed'
 except Exception as exc:
  report['status']='failed';report['error_type']=type(exc).__name__;save();raise
 finally:
  try:cleanup()
  finally:report['finished_at']=datetime.now(timezone.utc).isoformat();save();c.close()
 print(json.dumps({'status':report['status'],'checks':len(report['checks']),'cleanup':report['cleanup']}))

if __name__=='__main__':main()
