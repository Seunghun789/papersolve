import io
import pytest
from fastapi import HTTPException
from PIL import Image
from backend.documents import validate_document, MAX_BYTES
from backend.extraction import make_payload, page_image


def png():
    b=io.BytesIO();Image.new('RGB',(20,30),'white').save(b,format='PNG');return b.getvalue()


def test_mime_and_size_are_checked_from_bytes():
    validate_document(png(),'image/png')
    for content,mime in [(png(),'image/jpeg'),(b'not pdf','application/pdf'),(b'html','text/html'),(b'x'*(MAX_BYTES+1),'image/png')]:
        with pytest.raises(HTTPException):validate_document(content,mime)


def test_original_page_image_and_page_bounds():
    im=Image.open(io.BytesIO(page_image(png(),'image/png',1)))
    assert im.size==(20,30)
    with pytest.raises(ValueError):page_image(png(),'image/png',2)


def test_pdf_validation_and_page_rendering():
    b=io.BytesIO();im=Image.new('RGB',(20,30),'white');im.save(b,format='PDF',save_all=True,append_images=[im])
    content=b.getvalue();validate_document(content,'application/pdf')
    assert Image.open(io.BytesIO(page_image(content,'application/pdf',2))).size==(40,60)
    with pytest.raises(ValueError):page_image(content,'application/pdf',3)


def test_payload_preserves_unknown_dash_blank_zero_and_evidence():
    cells=[{'label':label,'source_raw':raw,'value':str(raw),'page':1,'box':[10,20,30,40]} for label,raw in [('zero','0'),('dash','-'),('blank',''),('unknown',None)]]
    s={'title':'재고','occurred_at':'2026-11-13','columns':['zero','dash','blank','unknown','missing'],'rows':[{'cells':cells}]}
    p=make_payload(s,{'1':[]});data=p['records'][0]['data']
    assert [data[k]['status'] for k in s['columns']]==['value','explicit_dash','blank','unknown','missing']
    assert all(data[k]['value'] is None for k in ['dash','blank','unknown','missing'])
    assert p['spans']['dash'][0]['rec']==0 and p['spans']['dash'][0]['box']==[10,20,30,40]


def test_missing_date_is_not_fabricated():
    with pytest.raises((ValueError,TypeError)):make_payload({'occurred_at':None}, {})


def test_header_is_not_used_as_evidence_for_row_number():
    cell={'label':'번호','source_raw':'1','page':1,'box':[70,220,140,270]}
    found={'1':[{'text':'번호','box':[70,220,140,270],'conf':1},
                {'text':'1','box':[83,322,105,361],'conf':1}]}
    result=make_payload({'title':'표','occurred_at':'2026-11-13','rows':[{'cells':[cell]}]},found)
    assert result['spans']['번호'][0]['box']==[83,322,105,361]
