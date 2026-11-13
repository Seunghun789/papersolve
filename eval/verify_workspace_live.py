"""Real JWT/API/UI comparison and isolated writes. Removes all disposable entities."""
import os,sys,json,time,uuid,secrets,hashlib,subprocess,argparse
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--web',default='http://127.0.0.1:3000');ap.add_argument('--api',default='http://127.0.0.1:8002');ap.add_argument('--output',default='artifacts/ui-migration/live');ap.add_argument('--writes',action='store_true');ap.add_argument('--legacy',action='store_true');ap.add_argument('--quick',action='store_true');a=ap.parse_args()
 out=ROOT/a.output;out.mkdir(parents=True,exist_ok=True)
 v=dict(l.split('=',1) for l in (ROOT/'.env').read_text().splitlines() if '=' in l and not l.startswith('#'))
 url=v['SUPABASE_URL'].strip();secret=v['SUPABASE_SECRET_KEY'].strip();pub=v['SUPABASE_KEY'].strip()
 c=httpx.Client(timeout=100,trust_env=False);headers={'apikey':secret,'Authorization':'Bearer '+secret,'Prefer':'return=representation'}
 report={'status':'running','web':a.web,'api':a.api,'checks':[],'comparison':{'viewport':[1440,1000],'workspace':105,'legacy_harness':'Original app.py rendering with injected disposable reviewer session; DB writes denied; rules/model calls disabled.'}};created={'users':[],'auth':[],'tenants':[]};proc=None
 def save(): (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
 def check(name,condition,**info):
  report['checks'].append({'name':name,'passed':bool(condition),**info});save()
  if not condition:raise AssertionError(name)
 def sb(method,path,**kw):return c.request(method,url+path,headers=headers,**kw)
 def ok(r):
  if not r.is_success:raise RuntimeError(f'{r.request.url.path}: {r.status_code} {r.text[:160]}')
  return r.json() if r.content else None
 def post(t,b):return ok(sb('POST','/rest/v1/'+t,json=b))[0]
 def all_rows(table,**filters):
  result=[];offset=0
  while True:
   rs=ok(sb('GET','/rest/v1/'+table,params={'select':'*','order':'id.asc','offset':offset,'limit':500,**filters}));result+=rs
   if len(rs)<500:return result
   offset+=len(rs)
 def hashes():
  # Existing business data, no secrets exported. Ignore added nullable migration column.
  tables={}
  for t in ['records','proposals','source_inputs']:
   rs=all_rows(t)
   for r in rs:r.pop('manual_request_key',None)
   tables[t]={'count':len(rs),'sha256':hashlib.sha256(json.dumps(rs,sort_keys=True,ensure_ascii=False).encode()).hexdigest()}
  return tables
 try:
  report['before']=hashes();save()
  run='ui-migration-'+uuid.uuid4().hex;email=run+'@example.com';pw=secrets.token_urlsafe(32)
  au=ok(sb('POST','/auth/v1/admin/users',json={'email':email,'password':pw,'email_confirm':True,'user_metadata':{'name':'이관 검증'}}));created['auth'].append(au['id']);save()
  u=post('users',{'provider':'email','sub':email,'name':'이관 검증'});created['users'].append(u['id']);save()
  post('auth_identity_links',{'auth_user_id':au['id'],'legacy_user_id':u['id']})
  for tid in [105,100]:post('memberships',{'tenant_id':tid,'user_id':u['id'],'role':'확인하는 사람'})
  auth=ok(c.post(url+'/auth/v1/token?grant_type=password',headers={'apikey':pub},json={'email':email,'password':pw}));ah={'Authorization':'Bearer '+auth['access_token']}
  if not a.quick:
   overview=ok(c.get(a.api+'/v1/workspaces/105/overview',headers=ah));existing=all_rows('records',tenant_id='eq.105',status='eq.확정')
   check('existing overview total equals independent DB count',overview['record_count']==len(existing),count=len(existing))
   overview100=ok(c.get(a.api+'/v1/workspaces/100/overview',headers=ah));existing100=all_rows('records',tenant_id='eq.100',status='eq.확정')
   check('large workspace aggregate has no 1000-row truncation',overview100['record_count']==len(existing100),count=len(existing100))
   check('foreign workspace overview denied',c.get(a.api+'/v1/workspaces/6/overview',headers=ah).status_code==403)
   check('foreign source cannot be fetched with own tenant',c.get(a.api+'/v1/workspaces/100/sources/441/original',headers=ah).status_code==404)
   original=c.get(a.api+'/v1/workspaces/105/sources/441/original',headers=ah)
   stored=sb('GET','/storage/v1/object/authenticated/uploads/uploads/105-441.jpg')
   check('legacy original bytes match Storage',original.status_code==200 and hashlib.sha256(original.content).digest()==hashlib.sha256(stored.content).digest())
   collected=[];after=0
   while True:
    page=ok(c.get(a.api+'/v1/workspaces/100/records',params={'status':'확정','after':after,'limit':100},headers=ah));collected+=page['items'];after=page['next']
    if after is None:break
   check('cursor pagination returns each existing row exactly once',len(collected)==len(existing100) and len({r['id'] for r in collected})==len(existing100))
  from playwright.sync_api import sync_playwright
  with sync_playwright() as p:
   browser=p.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1000});page.set_default_timeout(45000)
   errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   page.goto(a.web+'/login');page.get_by_label('이메일',exact=True).fill(email);page.get_by_label('비밀번호',exact=True).fill(pw);page.get_by_role('button',name='이메일로 로그인').click();page.locator('.workspace-main h1').wait_for()
   if not a.quick:
    for view in ['home','upload','review','records']:
     page.goto(a.web+f'/?workspace=105&view={view}');page.locator('.workspace-main h1').wait_for();page.evaluate('document.fonts.ready');page.wait_for_timeout(1200)
     if view=='records':page.locator('tbody tr').first.wait_for()
     page.screenshot(path=str(out/f'new-{view}.png'),full_page=True)
     if view=='home':
      page.get_by_text('쌓인 기록 · 표와 추이',exact=True).click();page.locator('tbody tr').first.wait_for();page.screenshot(path=str(out/'new-home-records.png'),full_page=True)
    page.locator('.table-cell').first.click();page.locator('.evidence-image img').wait_for();page.wait_for_function("document.querySelector('.evidence-image img')?.naturalWidth>0")
    page.screenshot(path=str(out/'new-evidence.png'),full_page=True)
    check('existing record cell opens real source image',page.locator('.evidence-image img').count()==1)
    page.goto(a.web+'/?workspace=105&view=upload');page.get_by_role('tab',name='올린 종이 4',exact=True).click();page.locator('.paper-row').first.wait_for();page.wait_for_timeout(1500);page.screenshot(path=str(out/'new-papers.png'),full_page=True)
    page.goto(a.web+'/?workspace=100&view=review');page.locator('.review-card').wait_for();page.wait_for_timeout(1000);page.screenshot(path=str(out/'new-review-populated.png'),full_page=True)
    check('existing review queue contains real legacy proposals',page.locator('.review-card').count()==1)
    page.set_viewport_size({'width':390,'height':844});page.goto(a.web+'/?workspace=105&view=records');page.locator('tbody tr').first.wait_for();page.screenshot(path=str(out/'new-mobile.png'),full_page=True)
    check('mobile document has no horizontal page overflow',page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
    page.get_by_role('button',name='메뉴 열기').click();check('mobile navigation can open',page.locator('.sidebar.open').count()==1)
    page.set_viewport_size({'width':1440,'height':1000})
   if a.legacy:
    f=(out/'legacy-server.log').open('w');proc=subprocess.Popen([sys.executable,'-m','streamlit','run','eval/capture_legacy_ui.py','--server.port','8502','--server.address','127.0.0.1','--server.headless','true'],cwd=ROOT,env={**os.environ,'PASSO_CAPTURE_USER':str(u['id'])},stdout=f,stderr=f)
    for _ in range(50):
     try:
      if c.get('http://127.0.0.1:8502/_stcore/health').status_code==200:break
     except httpx.RequestError:pass
     time.sleep(.2)
    old=browser.new_page(viewport={'width':1440,'height':1000});old.set_default_timeout(60000);old.goto('http://127.0.0.1:8502/?a=105');old.get_by_role('heading',level=2).first.wait_for();old.wait_for_timeout(1500);old.evaluate('document.fonts.ready');old.wait_for_function("document.querySelectorAll('[data-stale=true]').length===0")
    old.screenshot(path=str(out/'legacy-home.png'),full_page=True)
    for view,label in [('upload','올리기'),('review','확인')]:
     old.get_by_role('button',name=label,exact=True).last.click();old.wait_for_timeout(1500);old.wait_for_function("document.querySelectorAll('[data-stale=true]').length===0");old.screenshot(path=str(out/f'legacy-{view}.png'),full_page=True)
    old.get_by_role('button',name='홈',exact=True).last.click();old.get_by_text('쌓인 기록 · 표와 추이',exact=True).click();old.wait_for_timeout(1000);old.wait_for_function("document.querySelectorAll('[data-stale=true]').length===0");old.screenshot(path=str(out/'legacy-records.png'),full_page=True)
    old.goto('http://127.0.0.1:8502/?a=100');old.get_by_role('heading',level=2).first.wait_for();old.get_by_role('button',name='확인',exact=True).last.click();old.wait_for_timeout(1500);old.wait_for_function("document.querySelectorAll('[data-stale=true]').length===0");old.screenshot(path=str(out/'legacy-review-populated.png'),full_page=True)
    report['legacy_errors']=old.locator('[data-testid=stException]').all_text_contents();check('legacy comparison rendered without exception',not report['legacy_errors']);old.close()
   if a.writes:
    t=post('tenants',{'name':run,'title':'업무 화면 이관 검증'});created['tenants'].append(t['id']);save();tid=t['id'];post('memberships',{'tenant_id':tid,'user_id':u['id'],'role':'주인'})
    post('object_types',{'tenant_id':tid,'name':'재고 점검','kind':'기록'});post('property_defs',{'tenant_id':tid,'type_name':'재고 점검','name':'수량','dtype':'number'});post('alias_dict',{'tenant_id':tid,'type_name':'재고 점검','alias':'수량','prop_name':'수량'})
    page.goto(a.web+f'/?workspace={tid}&view=upload');page.get_by_role('tab',name='직접 적기',exact=True).click();page.get_by_label('수량',exact=True).fill('0')
    with page.expect_response(lambda r:'/manual-records' in r.url and r.request.method=='POST') as resp:page.get_by_role('button',name='저장',exact=True).click()
    check('manual entry UI persists through real API',resp.value.status==201,status=resp.value.status,response=resp.value.text()[:300],origin=resp.value.request.headers.get('origin'))
    r=resp.value.json();check('manual zero persisted as number',r['data']['수량']['value']==0)
    again=c.post(a.api+f'/v1/workspaces/{tid}/manual-records',headers=ah,json=resp.value.request.post_data_json);check('manual HTTP retry idempotent',again.status_code==201 and again.json()['id']==r['id'])
    payload={'type':'재고 점검','label':'수량','value':'0','record_id':r['id']};q=post('proposals',{'tenant_id':tid,'kind':'애매한 값','payload':payload,'source_input_id':r['source_input_id']})
    page.goto(a.web+f'/?workspace={tid}&view=review');page.get_by_label('확인할 값 수정').fill('12');page.get_by_role('button',name='고쳐서 저장',exact=True).click();page.get_by_text('지금 확인할 것은 다 봤어요',exact=True).wait_for()
    final=ok(sb('GET','/rest/v1/records',params={'id':f"eq.{r['id']}",'select':'data'}))[0]
    check('review UI atomically changes real row',str(final['data']['수량']['value'])=='12')
    page.goto(a.web+f'/?workspace={tid}&view=records');page.locator('.table-cell').first.click();page.get_by_label('값 수정',exact=True).fill('-');page.get_by_role('button',name='저장',exact=True).click();page.get_by_text('저장했어요. 원본은 그대로 보관됩니다.',exact=True).wait_for()
    final=ok(sb('GET','/rest/v1/records',params={'id':f"eq.{r['id']}",'select':'data'}))[0];check('record editor preserves explicit dash',final['data']['수량']['status']=='explicit_dash' and final['data']['수량']['value'] is None)
    page.screenshot(path=str(out/'manual-edit.png'),full_page=True)
   check('no browser JavaScript exceptions',not errors,errors=errors)
   browser.close()
  report['status']='passed'
 except Exception as e:
  report['status']='failed';report['error']=str(e);raise
 finally:
  if proc:proc.terminate();proc.wait(timeout=20)
  failures=[]
  for table,ids in [('tenants',created['tenants']),('users',created['users'])]:
   for ident in ids:
    rr=sb('DELETE','/rest/v1/'+table,params={'id':f'eq.{ident}'})
    if not rr.is_success or ok(sb('GET','/rest/v1/'+table,params={'id':f'eq.{ident}','select':'id'})):failures.append(f'{table}:{ident}')
  for ident in created['auth']:
   if not sb('DELETE','/auth/v1/admin/users/'+ident).is_success or sb('GET','/auth/v1/admin/users/'+ident).status_code!=404:failures.append('auth:'+ident)
  report['after']=hashes();report['cleanup']={'verified':not failures,'failures':failures,'created_ids':created}
  check('existing records proposals sources unchanged by full hashes',report['before']==report['after'])
  save();print(json.dumps({'status':report['status'],'checks':len(report['checks']),'cleanup':report['cleanup']['verified']}))
if __name__=='__main__':main()
