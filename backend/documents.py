"""One-document API. No in-process background tasks: PostgreSQL owns the queue."""
import hashlib
import io
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel

MAX_BYTES = 4 * 1024 * 1024
PUBLIC_JOB = ('id', 'status', 'source_input_id', 'created_at', 'updated_at', 'attempts', 'error_code')


def validate_document(content, mime):
    from PIL import Image, UnidentifiedImageError
    if not content or len(content) > MAX_BYTES:
        raise HTTPException(413, '파일은 4MB 이하로 올려 주세요.')
    try:
        if mime == 'application/pdf':
            import pypdfium2 as pdfium
            if not content.startswith(b'%PDF-'):
                raise ValueError('PDF signature')
            with pdfium.PdfDocument(content) as pdf:
                if not 1 <= len(pdf) <= 30:
                    raise ValueError('PDF page limit')
        elif mime in ('image/png', 'image/jpeg'):
            with Image.open(io.BytesIO(content)) as im:
                if im.format != {'image/png':'PNG','image/jpeg':'JPEG'}[mime] or im.width*im.height > 25_000_000:
                    raise ValueError('image format or dimensions')
                im.verify()
        else:
            raise ValueError('unsupported format')
    except Exception:
        raise HTTPException(422, 'PNG·JPEG 또는 30쪽 이하 PDF를 올려 주세요. 이미지는 2,500만 픽셀 이하입니다.') from None


def public_job(j):
    return {k:j.get(k) for k in PUBLIC_JOB}


def install_routes(app, session, gateway):
    @app.post('/v1/workspaces/{tid}/documents', status_code=202)
    async def upload(tid: int, request: Request, idempotency_key: UUID = Header(),
                     filename: str = 'document', token=Depends(session), g=Depends(gateway)):
        await g.authorize(token, tid, 'upload')
        if len(filename) > 200 or not filename.strip():
            raise HTTPException(422, '파일 이름을 확인해 주세요.')
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > MAX_BYTES:
                raise HTTPException(413, '파일은 4MB 이하로 올려 주세요.')
        content = bytes(content)
        mime = request.headers.get('content-type', '').split(';')[0]
        from starlette.concurrency import run_in_threadpool
        await run_in_threadpool(validate_document, content, mime)
        digest = hashlib.sha256(content).hexdigest()
        j = await g.rpc(token, 'passo_reserve_document', {'tid':tid,'request_key':str(idempotency_key),
            'digest':digest,'mime':mime,'size_bytes':len(content),'filename':filename})
        if j['status'] != 'uploading':
            return public_job(j)
        rows = (await g.request(token, 'GET', '/rest/v1/source_inputs', params={
            'id':f"eq.{j['source_input_id']}", 'tenant_id':f'eq.{tid}', 'select':'storage_path'})).json()
        path = rows[0]['storage_path']
        # Immutable upload; retry after a lost HTTP response verifies existing bytes.
        try:
            await g.request(token, 'POST', '/storage/v1/object/passo-originals/' + path,
                            content=content, headers={'Content-Type':mime})
        except HTTPException:
            existing = await g.original_bytes(path)
            if hashlib.sha256(existing).hexdigest() != digest:
                raise HTTPException(409, '이미 저장된 원본과 다릅니다.') from None
        return public_job(await g.rpc(token, 'passo_enqueue_document', {'tid':tid,'jid':j['id']}))

    @app.get('/v1/workspaces/{tid}/documents')
    async def documents(tid: int, token=Depends(session), g=Depends(gateway)):
        await g.authorize(token, tid)
        return (await g.request(token,'GET','/rest/v1/passo_jobs',params={
            'tenant_id':f'eq.{tid}', 'select':','.join(PUBLIC_JOB), 'order':'created_at.desc','limit':30})).json()

    @app.get('/v1/workspaces/{tid}/documents/{jid}')
    async def document(tid: int, jid: UUID, token=Depends(session), g=Depends(gateway)):
        await g.authorize(token, tid)
        jobs = (await g.request(token,'GET','/rest/v1/passo_jobs',params={
            'tenant_id':f'eq.{tid}','id':f'eq.{jid}','select':','.join(PUBLIC_JOB)})).json()
        if not jobs:
            raise HTTPException(404, '문서를 찾을 수 없습니다.')
        j = jobs[0]
        sources = (await g.request(token,'GET','/rest/v1/source_inputs',params={
            'tenant_id':f'eq.{tid}','id':f"eq.{j['source_input_id']}",'select':'id,name,spans'})).json()
        rows = (await g.request(token,'GET','/rest/v1/records',params={
            'tenant_id':f'eq.{tid}','source_input_id':f"eq.{j['source_input_id']}",
            'select':'id,type_name,occurred_at,data,status,source_row','order':'source_row.asc','limit':1000})).json()
        return {'job':j,'source':sources[0],'records':rows}

    @app.post('/v1/workspaces/{tid}/documents/{jid}/confirm')
    async def confirm(tid: int, jid: UUID, token=Depends(session), g=Depends(gateway)):
        await g.authorize(token, tid, 'review')
        return public_job(await g.rpc(token,'passo_confirm_document',{'tid':tid,'jid':str(jid)}))

    @app.get('/v1/workspaces/{tid}/documents/{jid}/original')
    async def original(tid: int, jid: UUID, page: int | None = None, token=Depends(session), g=Depends(gateway)):
        await g.authorize(token, tid)
        jobs = (await g.request(token,'GET','/rest/v1/passo_jobs',params={
            'tenant_id':f'eq.{tid}','id':f'eq.{jid}','select':'source_input_id'})).json()
        if not jobs:
            raise HTTPException(404, '문서를 찾을 수 없습니다.')
        sources = (await g.request(token,'GET','/rest/v1/source_inputs',params={
            'tenant_id':f'eq.{tid}','id':f"eq.{jobs[0]['source_input_id']}",'select':'storage_path'})).json()
        path = sources[0]['storage_path']
        if not await g.rpc(token,'passo_object_access',{'object_name':path,'operation':'read'}):
            raise HTTPException(403, '원본 접근 권한이 없습니다.')
        content = await g.original_bytes(path)
        mime = 'application/pdf' if path.endswith('.pdf') else ('image/png' if path.endswith('.png') else 'image/jpeg')
        if page is not None:
            from backend.extraction import page_image
            from starlette.concurrency import run_in_threadpool
            try:
                content = await run_in_threadpool(page_image, content, mime, page)
            except (ValueError, IndexError):
                raise HTTPException(404, '페이지를 찾을 수 없습니다.') from None
            mime = 'image/png'
        return Response(content, media_type=mime, headers={'Content-Disposition':'inline','Content-Security-Policy':"default-src 'none'; sandbox"})
