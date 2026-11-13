"""Controlled printed-image challenge and offline state/query checks, not blind human handwriting gold."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import types

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
OUT=ROOT/'artifacts/auth-policy-20261113/policy'


def save(name,value):
    p=OUT/name
    temp=p.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=lambda x:x.item())+'\n')
    temp.replace(p)


def fixtures():
    from PIL import Image, ImageDraw, ImageFont
    OUT.mkdir(parents=True,exist_ok=True)
    font=ImageFont.truetype('/System/Library/Fonts/AppleSDGothicNeo.ttc',32)
    titlefont=ImageFont.truetype('/System/Library/Fonts/AppleSDGothicNeo.ttc',42)
    docs=[]
    labels=['번호','입고','출고','재고']
    xs=[60,240,500,760,1020]; ys=[190+i*90 for i in range(8)]
    for n in range(3):
        image=Image.new('RGB',(1080,940),'white'); draw=ImageDraw.Draw(image)
        draw.text((60,55),f'수량 기록 {n+1}',font=titlefont,fill='black')
        draw.text((60,115),'2026-11-13',font=font,fill='black')
        for x in xs:draw.line((x,ys[0],x,ys[-1]),fill='black',width=2)
        for y in ys:draw.line((xs[0],y,xs[-1],y),fill='black',width=2)
        for c,label in enumerate(labels):draw.text((xs[c]+30,ys[0]+25),label,font=font,fill='black')
        cells=[]
        for r in range(6):
            draw.text((xs[0]+35,ys[r+1]+25),str(r+1),font=font,fill='black')
            for c,label in enumerate(labels[1:]):
                value=['0','-',''][(r+c+n)%3]
                draw.text((xs[c+1]+85,ys[r+1]+25),value,font=font,fill='black')
                cells.append({'row':str(r+1),'label':label,'raw':value,
                              'status':{'0':'value','-':'explicit_dash','':'blank'}[value],
                              'box':[xs[c+1]/1.08,ys[r+1]/.94,xs[c+2]/1.08,ys[r+2]/.94]})
        path=OUT/f'printed-{n+1}.png';image.save(path)
        docs.append({'path':str(path.relative_to(ROOT)),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'cells':cells})
    manifest={'kind':'synthetic printed table; constructed labels before model execution',
              'not_blind':True,'unreadable_gold':False,'documents':docs}
    save('fixtures.json',manifest)
    return manifest


def offline():
    from core import engine
    from core.record_cells import extracted_data, decode_record, encode_record, edit_data, cells_for
    from eval.test_record_cells import knowledge
    checks=[]
    def check(name,actual,expected):
        checks.append({'name':name,'actual':actual,'expected':expected,'passed':actual==expected})
    values=[{'수량':0},{'수량':5},{'수량':'-'},{'수량':''},{}, {'수량':None},
            extracted_data([{'label':'수량','value':'88','unreadable':True}]),
            extracted_data([{'label':'수량','value':'9'},{'label':'수량','value':'10'}])]
    K=knowledge(values)
    for fn,want in [('sum',5),('avg',2.5),('min',0),('max',5),('count',8)]:
        out,refs=engine.run_tool(K,'aggregate',{'type':'재고','fn':fn,'prop':'수량'})
        check(fn,out['결과'],want)
        if fn!='count':
            check(fn+':known',out['확인된 수량 칸'],2)
            check(fn+':unresolved',out['미확정 칸'],6)
        check(fn+':references',refs,[f'r{i}' for i in range(1,9)])
    for fn in ['sum','avg','min','max','latest']:
        out,_=engine.run_tool(knowledge(values[2:]),'aggregate',{'type':'재고','fn':fn,'prop':'수량'})
        check('all_unresolved:'+fn,out['결과'],None)
    for op,want in [('=',[1]),('!=',[2]),('<',[1]),('>',[2]),('<=',[1]),('>=',[1,2])]:
        n=0 if op in ['=','!=','<=','>='] else 1
        hits=engine.match(engine.frame(K,'재고'),[{'prop':'수량','op':op,'value':n}])
        check('condition:'+op,list(hits['_id']),want)
    data=extracted_data([{'label':'수량','value':'-'},{'label':'판독','value':'88','unreadable':True}],['누락'])
    revised=decode_record(encode_record({'data':edit_data(decode_record(encode_record({'data':data}))['data'],{'수량':0})}))['data']
    check('roundtrip edit zero',cells_for(revised)['수량'].value,0)
    check('roundtrip unreadable stays unresolved',cells_for(revised)['판독'].value,None)
    check('roundtrip missing remains missing',str(cells_for(revised)['누락'].status.value),'missing')
    save('query-checks.json',{'scope':'actual engine query tools + JSON codec; no LLM answer evaluation or live DB','checks':checks,'passed':all(c['passed'] for c in checks)})
    return checks


def live(manifest):
    import os
    import anthropic
    import httpx2 as httpx
    from eval.run_baseline import isolated_llm
    from core.record_cells import extracted_data,cells_for
    from core.cell_policy import normalize_cell,cells_match
    # Explicitly selected existing key; never serialize credentials or full transport errors.
    env=dict(line.split('=',1) for line in (ROOT/'.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
    key=os.environ.get('ANTHROPIC_API_KEY') or env['ANTHROPIC_API_KEY'].strip()
    model='claude-sonnet-5-5'
    report={'model':model,'started_at':datetime.now(timezone.utc).isoformat(),'cases':[], 'usage':[],
            'manifest_sha256':hashlib.sha256((OUT/'fixtures.json').read_bytes()).hexdigest(),
            'prompt_code_sha256':hashlib.sha256((ROOT/'core/llm.py').read_bytes()).hexdigest(),
            'limitations':['synthetic printed images, not handwriting','not independent blind validation','unreadable/ambiguous image gold needs human review','no full upload/OCR/auto-confirmation execution']}
    llm=isolated_llm()
    client=anthropic.Anthropic(api_key=key,base_url='https://api.anthropic.com',max_retries=0,timeout=120,
                              http_client=httpx.Client(trust_env=False,timeout=120))
    llm.client=lambda:client
    llm.log=lambda t,model,prompt,answer,usage_in,usage_out,started=None,**kw:report['usage'].append({'input':usage_in,'output':usage_out})
    real_stream=client.messages.stream
    def capped(**kw):
        kw['max_tokens']=6000
        return real_stream(**kw)
    client.messages.stream=capped
    for doc in manifest['documents']:
        started=time.monotonic();case={'path':doc['path']}
        try:
            p=ROOT/doc['path']
            sheet=llm.extract(types.SimpleNamespace(id='synthetic-eval'),model,p.name,p.read_bytes())
            actual={}; boxes={};duplicates=[]; extra=[]
            for row in sheet['rows']:
                idcells=[c for c in row['cells'] if c['label']=='번호']
                rid=str(idcells[0]['value']).strip() if len(idcells)==1 else '?'
                cells=cells_for(extracted_data([{**c,'written':c.get('source_raw',c['value'])} for c in row['cells']],['입고','출고','재고']))
                for label,cell in cells.items():
                    if label=='번호':continue
                    key=(rid,label)
                    if key in actual:duplicates.append(key)
                    actual[key]=cell
                for c in row['cells']: boxes[(rid,c['label'])]=c.get('box')
            checks=[]
            wanted={(c['row'],c['label']) for c in doc['cells']}
            for key in actual:
                if key not in wanted:extra.append({'row':key[0],'label':key[1],'cell':actual[key].to_dict()})
            for gold in doc['cells']:
                key=(gold['row'],gold['label']); cell=actual.get(key,normalize_cell())
                box=boxes.get(key); target=gold['box']
                center_ok=False
                if isinstance(box,list) and len(box)==4 and all(isinstance(x,(int,float)) for x in box):
                    cx=(box[0]+box[2])/2;cy=(box[1]+box[3])/2
                    center_ok=target[0]<=cx<=target[2] and target[1]<=cy<=target[3]
                checks.append({**gold,'actual':cell.to_dict(),'match':cells_match(normalize_cell(gold['raw']),cell) and key not in duplicates,
                               'predicted_box':box,'box_center_in_gold_cell':center_ok})
            case.update(status='ok',extraction=sheet,checks=checks,extra_cells=extra,duplicate_keys=duplicates)
        except Exception as e:case.update(status='failed',error_type=type(e).__name__)
        case['seconds']=time.monotonic()-started;report['cases'].append(case);save('image-results.json',report)
        if case['status']!='ok':break
    client.close()
    checks=[c for case in report['cases'] for c in case.get('checks',[])]
    report['summary']={'by_state':{s:{'correct':sum(c['match'] for c in checks if c['status']==s),'total':sum(c['status']==s for c in checks)} for s in ['value','explicit_dash','blank']},
                       'box_center_correct':sum(c['box_center_in_gold_cell'] for c in checks),'cells':len(checks),
                       'extra_cells':sum(len(c.get('extra_cells',[])) for c in report['cases']),
                       'complete':len(report['cases'])==3 and all(c['status']=='ok' for c in report['cases'])}
    save('image-results.json',report)
    print(json.dumps(report['summary'],ensure_ascii=False))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--live',action='store_true');args=ap.parse_args()
    manifest=fixtures();checks=offline()
    print('offline',sum(c['passed'] for c in checks),'/',len(checks))
    if args.live:live(manifest)
