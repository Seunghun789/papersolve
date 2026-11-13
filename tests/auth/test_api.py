import httpx
import pytest
from fastapi.testclient import TestClient
from backend.main import Gateway, app, gateway


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('SUPABASE_URL','https://test.supabase.co')
    monkeypatch.setenv('SUPABASE_PUBLISHABLE_KEY','sb_publishable_test')
    calls=[]
    state={'role':'주인','active':True,'conflict':False}
    def handler(req):
        calls.append(req)
        token=req.headers.get('authorization','')
        path=req.url.path
        if token!='Bearer valid':
            return httpx.Response(401,json={'code':'bad_jwt'})
        if path=='/auth/v1/user': return httpx.Response(state.get('auth_status',200),json={'id':'user-A'})
        if path.endswith('passo_session_active'): return httpx.Response(200,json=state['active'])
        if path.endswith('passo_access'):
            import json
            body=json.loads(req.content)
            allowed=body['tid']==1 and (body['operation']=='read' or state['role'] in ('주인','확인하는 사람'))
            return httpx.Response(200,json=allowed)
        if path.endswith('passo_workspaces'): return httpx.Response(200,json=[{'id':1,'role':state['role']}])
        if path.endswith('passo_edit_cells'):
            if state['conflict']: return httpx.Response(409,json={'code':'PT409'})
            import json
            b=json.loads(req.content)
            return httpx.Response(200,json=[{'id':1,'data':b['changes']}] if b['rid']==1 else [])
        if path=='/rest/v1/tenants': return httpx.Response(200,json=[{'id':1,'title':None}])
        if path=='/rest/v1/records':
            assert req.url.params['tenant_id']=='eq.1'
            assert req.url.params['order']=='id.asc'
            return httpx.Response(200,json=[{'id':1},{'id':2}])
        if path=='/rest/v1/passo_jobs':
            assert req.url.params['tenant_id']=='eq.1'
            return httpx.Response(200,json=[])
        return httpx.Response(404,json={})
    remote=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    g=Gateway(remote)
    app.dependency_overrides[gateway]=lambda:g
    with TestClient(app) as c:
        yield c,state,calls
    app.dependency_overrides.clear()

AUTH={'Authorization':'Bearer valid'}

@pytest.mark.parametrize('token',[None,'','Basic valid','Bearer forged','Bearer a b'])
def test_unauthenticated(client,token):
    c,_,_=client
    assert c.get('/v1/workspaces',headers={'Authorization':token} if token is not None else {}).status_code==401


def test_no_store_and_pagination(client):
    c,_,calls=client
    r=c.get('/v1/workspaces/1/records?limit=1&after=0',headers=AUTH)
    assert r.status_code==200 and r.json()=={'items':[{'id':1}],'next':1}
    assert r.headers['cache-control']=='private, no-store'
    assert all(req.headers.get('apikey')=='sb_publishable_test' for req in calls)
    assert all(req.headers.get('authorization')=='Bearer valid' for req in calls)


@pytest.mark.parametrize('path',['records','records/1/cells','jobs/00000000-0000-4000-8000-000000000001'])
def test_cross_tenant_denied_before_query(client,path):
    c,_,calls=client
    if path.endswith('cells'):
        r=c.patch('/v1/workspaces/2/'+path,headers=AUTH,json={'expected_data':{},'changes':{'x':0}})
    else:r=c.get('/v1/workspaces/2/'+path,headers=AUTH)
    assert r.status_code==403
    assert not any('/rest/v1/records' in str(req.url) or '/rest/v1/passo_jobs' in str(req.url) for req in calls)


def test_revoked_session(client):
    c,state,_=client
    state['active']=False
    assert c.get('/v1/workspaces',headers=AUTH).status_code==401


def test_role_change_and_conflict(client):
    c,state,_=client
    body={'expected_data':{'q':'-'},'changes':{'q':0}}
    url='/v1/workspaces/1/records/1/cells'
    assert c.patch(url,headers=AUTH,json=body).status_code==200
    state['role']='보는 사람'
    assert c.patch(url,headers=AUTH,json=body).status_code==403
    state['role']='확인하는 사람';state['conflict']=True
    assert c.patch(url,headers=AUTH,json=body).status_code==409


@pytest.mark.parametrize('changes',[{'q':True},{'q':{'value':0}},{}])
def test_invalid_cell_payload(client,changes):
    c,_,_=client
    assert c.patch('/v1/workspaces/1/records/1/cells',headers=AUTH,json={'expected_data':{},'changes':changes}).status_code==422


def test_tenant_spoofing_in_body(client):
    c,_,_=client
    assert c.patch('/v1/workspaces/1/records/1/cells',headers=AUTH,json={'tenant_id':2,'expected_data':{},'changes':{'q':0}}).status_code==422


def test_pagination_bound_and_missing_job(client):
    c,_,_=client
    assert c.get('/v1/workspaces/1/records?limit=10000',headers=AUTH).status_code==422
    assert c.get('/v1/workspaces/1/jobs/bad',headers=AUTH).status_code==422
    assert c.get('/v1/workspaces/1/jobs/00000000-0000-4000-8000-000000000001',headers=AUTH).status_code==404


def test_gateway_outage_fails_closed(client,monkeypatch):
    c,_,_=client
    async def unavailable(*a,**kw): raise httpx.ConnectError('secret URL not to be shown')
    g=app.dependency_overrides[gateway]()
    monkeypatch.setattr(g.client,'request',unavailable)
    r=c.get('/v1/workspaces',headers=AUTH)
    assert r.status_code==503
    assert 'secret' not in r.text


def test_revoked_auth_403_becomes_login_required(client):
    c,state,_=client
    state["auth_status"]=403
    assert c.get("/v1/workspaces",headers=AUTH).status_code==401


def test_file_adapter_cannot_run_before_authorization(client,monkeypatch):
    c,state,_=client
    g=app.dependency_overrides[gateway]()
    async def forbidden_call(path):
        pytest.fail('Storage secret used before user authorization')
    monkeypatch.setattr(g,'original_bytes',forbidden_call)
    assert c.get('/v1/workspaces/2/originals/uuid/a.png',headers=AUTH).status_code==403
    state['active']=False
    assert c.get('/v1/workspaces/1/originals/uuid/a.png',headers=AUTH).status_code==401


def test_workspace_internal_name_is_not_displayed(client):
    c,_,_=client
    assert c.get('/v1/workspaces',headers=AUTH).json()[0]['title']=='내 워크스페이스'
