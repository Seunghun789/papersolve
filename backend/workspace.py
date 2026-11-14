"""Workspace read models. Every query uses the caller's JWT and tenant RLS."""
import asyncio
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import quote

from fastapi import Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID

CHECK_KINDS = ('종류','칸','같은 칸','애매한 값','읽은 값','대상 분류','대상','같은 대상',
               '대상 속성','속성 값','상위 분류','관계 속성','연결','워크스페이스 설정','맡기기')


async def rows(g, token, tid, table, select, **filters):
    # Keyset pagination avoids PostgREST's 1,000-row default truncating totals.
    result, after = [], 0
    while True:
        batch = (await g.request(token, 'GET', '/rest/v1/' + table, params={
            'tenant_id':f'eq.{tid}', 'select':select, **filters,
            'id':f'gt.{after}', 'order':'id.asc', 'limit':500})).json()
        result.extend(batch)
        if len(batch) < 500:
            return result
        after = batch[-1]['id']


def period_range(period, today=None):
    today = today or datetime.now(ZoneInfo('Asia/Seoul')).date()
    if period == '오늘': start = today
    elif period == '이번 달': start = today.replace(day=1)
    elif period == '올해': start = today.replace(month=1, day=1)
    else: start = today - timedelta(days=today.weekday())
    end = today
    if period == '이번 달':
        previous_end = start - timedelta(days=1)
        previous_start = previous_end.replace(day=1)
    elif period == '올해':
        previous_start, previous_end = date(today.year-1,1,1), date(today.year-1,12,31)
    elif period == '이번 주': previous_start, previous_end = start-timedelta(days=7), start-timedelta(days=1)
    else: previous_start = previous_end = start-timedelta(days=1)
    return tuple(d.isoformat() for d in (start,end,previous_start,previous_end))


class ManualRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_key: UUID
    type_name: str = Field(min_length=1,max_length=200)
    occurred_at: date
    data: dict = Field(min_length=1,max_length=100)


class ReviewAnswer(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_payload: dict
    expected_records: dict = Field(default_factory=dict)
    answer: str = Field(pattern='^(accept|edit|blank|reject)$')
    value: str | int | float | None = None


class StructureAnswer(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_revision: str = Field(pattern='^[0-9a-f]{32}$')
    answer: str = Field(pattern='^(accept|reject|split)$')


class Revision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_revision: str = Field(pattern='^[0-9a-f]{32}$')


class ReadAnswer(Revision):
    answer: str = Field(pattern='^(accept|reject)$')


def install_routes(app, session, gateway):
    @app.get('/v1/workspaces/{tid}/audit')
    async def audit(tid: int, after: int=Query(0,ge=0), token=Depends(session), g=Depends(gateway)):
        await g.authorize(token,tid)
        items=(await g.request(token,'GET','/rest/v1/passo_audit',params={'tenant_id':f'eq.{tid}',
            'select':'*','id':f'lt.{after}' if after else 'gt.0','order':'id.desc','limit':51})).json()
        return {'items':items[:50],'next':items[49]['id'] if len(items)>50 else None}

    @app.post('/v1/workspaces/{tid}/audit/{aid}/undo')
    async def undo(tid: int, aid: int, token=Depends(session), g=Depends(gateway)):
        await g.authorize(token,tid,'review')
        return await g.rpc(token,'passo_undo',{'tid':tid,'aid':aid})

    @app.get('/v1/workspaces/{tid}/sources/{sid}/review')
    async def source_preview(tid: int,sid: int,token=Depends(session),g=Depends(gateway)):
        await g.authorize(token,tid)
        return await g.rpc(token,'passo_source_preview',{'tid':tid,'sid':sid})

    @app.post('/v1/workspaces/{tid}/sources/{sid}/reopen')
    async def reopen(tid: int,sid: int,body: Revision,token=Depends(session),g=Depends(gateway)):
        await g.authorize(token,tid,'review')
        return await g.rpc(token,'passo_reopen_source',{'tid':tid,'sid':sid,**body.model_dump()})

    @app.get('/v1/workspaces/{tid}/sources/{sid}/policy')
    async def policy(tid: int,sid: int,token=Depends(session),g=Depends(gateway)):
        await g.authorize(token,tid)
        return await g.rpc(token,'passo_policy_preview',{'tid':tid,'sid':sid})

    @app.post('/v1/workspaces/{tid}/sources/{sid}/policy')
    async def apply_policy(tid: int,sid: int,body: Revision,token=Depends(session),g=Depends(gateway)):
        await g.authorize(token,tid,'review')
        return await g.rpc(token,'passo_apply_policy',{'tid':tid,'sid':sid,**body.model_dump()})

    @app.post('/v1/workspaces/{tid}/reviews/{qid}/read')
    async def answer_read(tid: int,qid: int,body: ReadAnswer,token=Depends(session),g=Depends(gateway)):
        await g.authorize(token,tid,'review')
        return await g.rpc(token,'passo_answer_read',{'tid':tid,'qid':qid,**body.model_dump()})

    @app.get('/v1/workspaces/{tid}/overview')
    async def overview(tid: int, period: str = Query('이번 주',pattern='^(오늘|이번 주|이번 달|올해)$'), token=Depends(session), g=Depends(gateway)):
        await g.authorize(token,tid)
        records, sources, proposals, props, aliases, objects = await asyncio.gather(
            rows(g,token,tid,'records','id,type_name,occurred_at,status,source_input_id',status='eq.확정'),
            rows(g,token,tid,'source_inputs','id,name,channel,created_at,auto,storage_path'),
            rows(g,token,tid,'proposals','id,kind,status,source_input_id,created_at',status='eq.대기'),
            rows(g,token,tid,'property_defs','id,type_name,name,dtype,unit,position'),
            rows(g,token,tid,'alias_dict','id,type_name,alias,prop_name'),
            rows(g,token,tid,'object_types','id,name,kind'))
        checks = [p for p in proposals if p['kind'] in CHECK_KINDS]
        types = list(dict.fromkeys([o['name'] for o in objects if o['kind']=='기록']+[r['type_name'] for r in records]))
        a,b,c,d = period_range(period)
        stats = []
        for name in types:
            own = [r for r in records if r['type_name']==name]
            stats.append({'name':name,'count':len(own),'current':sum(a<=r['occurred_at']<=b for r in own),
                          'previous':sum(c<=r['occurred_at']<=d for r in own),
                          'last':max((r['occurred_at'] for r in own),default=None)})
        for s in sources:
            own = [r for r in records if r['source_input_id']==s['id']]
            s['rows'] = len(own)
            s['types'] = list(dict.fromkeys(r['type_name'] for r in own))
            s['wait'] = sum(p['source_input_id']==s['id'] for p in checks)
            s['has_original'] = bool(s.pop('storage_path',None))
        since = (datetime.now(ZoneInfo('Asia/Seoul')).date()-timedelta(days=1)).isoformat()
        decisions=(await g.request(token,'GET','/rest/v1/proposals',params={'tenant_id':f'eq.{tid}',
            'select':'id,kind,payload,status,decided_at','status':'in.(승인,고침)',
            'decided_at':f'gte.{since}','order':'decided_at.desc','limit':40})).json()
        recent=[]
        for source in sources:
            if source['created_at'][:10]>=since and source['rows']:
                recent.append({'id':'source-'+str(source['id']),'at':source['created_at'],
                    'text':f"'{source['name']}'에서 기록 {source['rows']}줄이 들어왔어요"})
        for q in decisions:
            p=q['payload'];kind=q['kind'];text=None
            if kind=='종류':text=f"기록 종류 '{p.get('type')}'를 만들었어요"
            elif kind=='칸':text=f"'{p.get('type')}'에 칸 '{p.get('name')}'을 더했어요"
            elif kind=='같은 칸':text=f"'{p.get('label')}'을 '{p.get('similar_to')}' 칸으로 읽기로 했어요"
            elif kind=='애매한 값' and q['status']=='고침':text=f"'{p.get('label')}' 값을 고쳤어요"
            elif kind=='연결':text=f"관계 '{p.get('from')} → {p.get('to')}'를 이었어요"
            if text:recent.append({'id':'review-'+str(q['id']),'at':q['decided_at'],'text':text})
        recent.sort(key=lambda r:r['at'],reverse=True)
        return {'types':stats,'record_count':len(records),'review_count':len(checks),'sources':list(reversed(sources)),
                'properties':sorted(props,key=lambda p:(p['position'],p['id'])),'aliases':aliases,
                'period':period,'range':[a,b], 'recent':recent[:6]}

    @app.get('/v1/workspaces/{tid}/reviews')
    async def reviews(tid: int, after: int=Query(0,ge=0), token=Depends(session), g=Depends(gateway)):
        await g.authorize(token,tid)
        items=(await g.request(token,'GET','/rest/v1/proposals',params={'tenant_id':f'eq.{tid}',
            'select':'id,kind,payload,evidence,status,source_input_id,created_at','status':'eq.대기',
            'kind':'in.('+','.join(CHECK_KINDS)+')','id':f'gt.{after}','order':'id.asc','limit':51})).json()
        return {'items':items[:50],'next':items[49]['id'] if len(items)>50 else None}

    @app.post('/v1/workspaces/{tid}/reviews/{qid}')
    async def answer(tid: int, qid: int, body: ReviewAnswer, token=Depends(session), g=Depends(gateway)):
        await g.authorize(token,tid,'review')
        return await g.rpc(token,'passo_answer_review',{'tid':tid,'qid':qid,**body.model_dump()})

    @app.get('/v1/workspaces/{tid}/reviews/{qid}/structure')
    async def structure_preview(tid: int, qid: int, token=Depends(session), g=Depends(gateway)):
        await g.authorize(token,tid)
        return await g.rpc(token,'passo_structure_preview',{'tid':tid,'qid':qid})

    @app.post('/v1/workspaces/{tid}/reviews/{qid}/structure')
    async def structure_answer(tid: int, qid: int, body: StructureAnswer, token=Depends(session), g=Depends(gateway)):
        await g.authorize(token,tid,'review')
        return await g.rpc(token,'passo_answer_structure',{'tid':tid,'qid':qid,**body.model_dump()})

    @app.post('/v1/workspaces/{tid}/manual-records',status_code=201)
    async def manual(tid: int, body: ManualRecord, token=Depends(session), g=Depends(gateway)):
        await g.authorize(token,tid,'upload')
        return await g.rpc(token,'passo_write_record',{'tid':tid,**body.model_dump(mode='json')})

    @app.get('/v1/workspaces/{tid}/sources/{sid}')
    async def source(tid: int,sid: int,token=Depends(session),g=Depends(gateway)):
        await g.authorize(token,tid)
        found=(await g.request(token,'GET','/rest/v1/source_inputs',params={'tenant_id':f'eq.{tid}',
            'id':f'eq.{sid}','select':'id,name,channel,spans,created_at,auto,storage_path'})).json()
        if not found: raise HTTPException(404,'원본을 찾을 수 없습니다.')
        result=found[0];result['has_original']=bool(result.pop('storage_path',None))
        result['records']=await rows(g,token,tid,'records','id,type_name,occurred_at,data,status,source_row,uncertain,source_input_id',source_input_id=f'eq.{sid}')
        return result

    @app.get('/v1/workspaces/{tid}/sources/{sid}/original')
    async def original(tid: int,sid: int,page: int|None=Query(None,ge=1,le=30),token=Depends(session),g=Depends(gateway)):
        await g.authorize(token,tid)
        found=(await g.request(token,'GET','/rest/v1/source_inputs',params={'tenant_id':f'eq.{tid}',
            'id':f'eq.{sid}','select':'storage_path'})).json()
        if not found or not found[0]['storage_path']: raise HTTPException(404,'저장된 원본이 없습니다.')
        path=found[0]['storage_path']
        # Never accept a caller-supplied storage path. Legacy paths must bind both tenant AND source ID.
        import re
        if re.fullmatch(rf'uploads/{tid}-{sid}\.(jpg|jpeg|png|pdf|webp)',path):
            content=await g.original_bytes(path,bucket='uploads')
        elif re.fullmatch(rf'{tid}/[0-9a-f-]{{36}}/original\.(png|jpg|pdf)',path):
            if not await g.rpc(token,'passo_object_access',{'object_name':path,'operation':'read'}):
                raise HTTPException(403,'원본 접근 권한이 없습니다.')
            content=await g.original_bytes(path)
        else: raise HTTPException(404,'이 원본은 아직 연결되지 않았습니다.')
        mime='application/pdf' if path.endswith('.pdf') else 'image/png' if path.endswith('.png') else 'image/webp' if path.endswith('.webp') else 'image/jpeg'
        if page is not None:
            from backend.extraction import page_image
            from starlette.concurrency import run_in_threadpool
            try: content=await run_in_threadpool(page_image,content,mime,page)
            except (ValueError,IndexError): raise HTTPException(404,'페이지를 찾을 수 없습니다.') from None
            mime='image/png'
        return Response(content,media_type=mime,headers={'Content-Disposition':'inline','Content-Security-Policy':"default-src 'none'; sandbox"})
