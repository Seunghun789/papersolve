"""Authentication boundary. Supabase validates tokens; RLS independently authorizes every query."""
import os
from contextlib import asynccontextmanager
from typing import Annotated
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr


class Gateway:
    def __init__(self, client):
        self.client = client
        self.url = os.environ.get('SUPABASE_URL', '').rstrip('/')
        self.key = os.environ.get('SUPABASE_PUBLISHABLE_KEY', '')

    async def request(self, token, method, path, **kwargs):
        if not self.url or not self.key or self.key.startswith('sb_secret_'):
            raise HTTPException(503, '인증 서비스 설정이 필요합니다.')
        try:
            r = await self.client.request(method, self.url + path,
                headers={'apikey': self.key, 'Authorization': 'Bearer ' + token}, **kwargs)
        except httpx.RequestError:
            raise HTTPException(503, '인증 서비스에 연결할 수 없습니다.') from None
        if not r.is_success:
            code = r.json().get('code') if r.headers.get('content-type', '').startswith('application/json') else ''
            status = 409 if code in ('PT409', '40001') else (r.status_code if r.status_code in (401,403,404) else 503)
            raise HTTPException(status, '요청을 처리할 수 없습니다.')
        return r

    async def rpc(self, token, name, body=None):
        return (await self.request(token, 'POST', '/rest/v1/rpc/' + name, json=body or {})).json()

    async def authenticate(self, token):
        try:
            user = (await self.request(token, 'GET', '/auth/v1/user')).json()
        except HTTPException as exc:
            if exc.status_code in (401, 403):
                raise HTTPException(401, '다시 로그인해 주세요.') from None
            raise
        if not user.get('id') or not await self.rpc(token, 'passo_session_active'):
            raise HTTPException(401, '다시 로그인하거나 계정 연결을 확인해 주세요.')
        return user['id']

    async def authorize(self, token, tid, operation='read'):
        if not await self.rpc(token, 'passo_access', {'tid':tid, 'operation':operation}):
            raise HTTPException(403, '워크스페이스 접근 권한이 없습니다.')

    async def original_bytes(self, path):
        # Called only AFTER session + tenant + object authorization. Never used for DB queries.
        key = os.environ.get('SUPABASE_STORAGE_SECRET_KEY', '')
        if not key.startswith('sb_secret_'):
            raise HTTPException(503, '원본 저장소 설정이 필요합니다.')
        try:
            r = await self.client.get(self.url + '/storage/v1/object/authenticated/passo-originals/' + quote(path, safe='/'),
                headers={'apikey':key, 'Authorization':'Bearer ' + key})
        except httpx.RequestError:
            raise HTTPException(503, '원본 저장소에 연결할 수 없습니다.') from None
        if not r.is_success:
            raise HTTPException(404 if r.status_code in (400,404) else 503, '원본을 가져올 수 없습니다.')
        return r.content


@asynccontextmanager
async def lifespan(app):
    async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
        app.state.gateway = Gateway(client)
        yield

app = FastAPI(title='passo API', version='0.1.0', lifespan=lifespan)

@app.middleware('http')
async def no_store(request, call_next):
    response = await call_next(request)
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


def gateway():
    return app.state.gateway


async def session(authorization: Annotated[str | None, Header()] = None, g=Depends(gateway)):
    if not authorization or not authorization.startswith('Bearer ') or len(authorization)>8192:
        raise HTTPException(401, '로그인이 필요합니다.')
    token = authorization[7:]
    if not token or any(c.isspace() for c in token):
        raise HTTPException(401, '로그인이 필요합니다.')
    await g.authenticate(token)
    return token


@app.get('/healthz')
def health():
    return {'status':'ok'}


@app.get('/v1/workspaces')
async def workspaces(token=Depends(session), g=Depends(gateway)):
    return await g.rpc(token, 'passo_workspaces')


@app.get('/v1/workspaces/{tid}/records')
async def records(tid: int, after: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                  token=Depends(session), g=Depends(gateway)):
    await g.authorize(token, tid)
    rows = (await g.request(token, 'GET', '/rest/v1/records', params={
        'tenant_id':f'eq.{tid}', 'id':f'gt.{after}', 'select':'id,type_name,occurred_at,data,status,source_input_id',
        'order':'id.asc', 'limit':limit+1})).json()
    return {'items':rows[:limit], 'next':rows[limit-1]['id'] if len(rows)>limit else None}


class CellEdit(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    expected_data: dict = Field(max_length=200)
    changes: dict[str, StrictStr | StrictInt | StrictFloat | None] = Field(min_length=1, max_length=100)


@app.patch('/v1/workspaces/{tid}/records/{rid}/cells')
async def edit_cells(tid: int, rid: int, edit: CellEdit, token=Depends(session), g=Depends(gateway)):
    await g.authorize(token, tid, 'review')
    rows = await g.rpc(token, 'passo_edit_cells', {'tid':tid, 'rid':rid, **edit.model_dump()})
    if not rows:
        raise HTTPException(404, '기록을 찾을 수 없습니다.')
    return rows[0]


@app.get('/v1/workspaces/{tid}/jobs/{job_id}')
async def job(tid: int, job_id: str, token=Depends(session), g=Depends(gateway)):
    from uuid import UUID
    try:
        job_id = str(UUID(job_id))
    except ValueError:
        raise HTTPException(422, '잘못된 작업 번호입니다.') from None
    await g.authorize(token, tid)
    rows = (await g.request(token,'GET','/rest/v1/passo_jobs',params={
        'tenant_id':f'eq.{tid}', 'id':f'eq.{job_id}', 'select':'id,status,created_at', 'limit':1})).json()
    if not rows:
        raise HTTPException(404, '작업을 찾을 수 없습니다.')
    return rows[0]


@app.get('/v1/workspaces/{tid}/originals/{object_id}/{filename}')
async def original(tid: int, object_id: str, filename: str, token=Depends(session), g=Depends(gateway)):
    await g.authorize(token, tid)
    path = f'{tid}/{object_id}/{filename}'
    if not await g.rpc(token, 'passo_object_access', {'object_name':path,'operation':'read'}):
        raise HTTPException(403, '원본 접근 권한이 없습니다.')
    content = await g.original_bytes(path)
    return Response(content, media_type='application/octet-stream',
                    headers={'Content-Disposition':'attachment'})
