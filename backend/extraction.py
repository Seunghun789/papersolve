"""Pure extraction + provenance adapter; never imports legacy DB or writes records."""
import io
import os
import time
from datetime import date


def page_image(content, mime, page):
    from PIL import Image, ImageOps
    if mime == 'application/pdf':
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(content) as pdf:
            if not 1 <= page <= len(pdf):
                raise ValueError('page')
            p = pdf[page-1]
            scale = min(2, 2000/max(p.get_size()))
            bitmap = p.render(scale=scale)
            im = bitmap.to_pil().copy()
            bitmap.close(); p.close()
    else:
        if page != 1:
            raise ValueError('page')
        with Image.open(io.BytesIO(content)) as source:
            im = ImageOps.exif_transpose(source).convert('RGB')
            im.thumbnail((2000,2000))
    out = io.BytesIO(); im.save(out,format='PNG'); im.close()
    return out.getvalue()


def run_ocr(content, mime):
    from core import ocr
    pages = 1
    if mime == 'application/pdf':
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(content) as pdf:
            pages = len(pdf)
    return {str(n):ocr.lines(page_image(content,mime,n)) for n in range(1,pages+1)}


def run_model(content, mime):
    import anthropic
    import httpx2
    from core.llm import extract, DEFAULT
    usage = {}
    def sink(t, model, prompt, answer, usage_in, usage_out, started):
        usage.update(model=model,input_tokens=usage_in,output_tokens=usage_out,seconds=round(time.monotonic()-started,3))
    # Explicit transport: use authorized provider key, no implicit retries or proxy.
    with anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'],base_url='https://api.anthropic.com',
        max_retries=0,timeout=180,http_client=httpx2.Client(trust_env=False,timeout=180)) as client:
        sheet = extract(None,os.environ.get('PASSO_MODEL',DEFAULT),'original.pdf' if mime=='application/pdf' else 'original.png',
                        content,api_client=client,log_sink=sink)
    return {'sheet':sheet,'usage':usage}


def make_payload(sheet, found):
    from core import ocr
    from core.record_cells import extracted_data, encode_record
    raw_date = sheet.get('occurred_at')
    # Never fabricate a business date: missing/invalid dates fail for explicit repair.
    occurred = date.fromisoformat(raw_date).isoformat()
    if not sheet.get('title') or not 1 <= len(sheet.get('rows',[])) <= 1000:
        raise ValueError('empty or oversized extraction')
    records, spans = [], {}
    for index, row in enumerate(sheet['rows']):
        cells = [*sheet.get('doc_fields',[]), *row['cells']]
        normalized = [{**c,'value':c.get('normal') or c.get('source_raw',c.get('value')),
                       'written':c.get('source_raw',c.get('value'))} for c in cells]
        data = encode_record({'data':extracted_data(normalized,sheet.get('columns',[]))})['data']
        records.append({'type_name':sheet['title'],'occurred_at':occurred,'data':data,
                        'uncertain':[c['label'] for c in cells if c.get('uncertain') or c.get('unreadable')]})
        for page in {c.get('page',1) for c in cells}:
            group = [c for c in cells if c.get('page',1)==page]
            def box(c):
                b = c.get('box')
                return b if isinstance(b,list) and len(b)==4 and all(isinstance(v,(int,float)) and 0<=v<=1000 for v in b) and b[0]<b[2] and b[1]<b[3] else None
            # A column header is not evidence for a short cell (e.g. row number 1).
            # Legacy OCR matching gives label text a bonus; exclude exact header-only lines here.
            labels = {c['label'].strip() for c in cells}
            lines = [line for line in found.get(str(page),[]) if line['text'].strip() not in labels]
            placed = ocr.place_all([(str(c.get('source_raw') or ''),box(c),c['label']) for c in group],lines)
            for n,c in enumerate(group):
                spans.setdefault(c['label'],[]).append({'rec':index,'page':page,'quote':c.get('source_raw'),
                    'box':placed[n] if n < len(placed) else box(c),'model_box':box(c),'location':'estimated',
                    'unsure':c.get('uncertain',False),'unreadable':c.get('unreadable',False)})
    return {'records':records,'spans':spans}
