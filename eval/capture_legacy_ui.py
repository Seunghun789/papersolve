"""Read-only visual harness: execute unmodified app.py with a supplied test identity.
Only session/identity selection is substituted; all DB mutations are denied.
Start bound to loopback with PASSO_CAPTURE_USER=the disposable test user ID.
"""
import os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import streamlit as st
from core import db,engine
# Prevent app startup suggestions, membership timestamps, and model calls from changing production.
original_call=db.call

def read_only(method,*args,**kwargs):
 if method.upper()!='GET':raise RuntimeError('Visual comparison is read-only')
 return original_call(method,*args,**kwargs)
db.call=read_only
engine.rules=lambda *a,**kw:None
st.session_state.entered=True
st.session_state._space_seen=int(st.query_params.get('a','105'))
st.session_state._acct={'key':(int(os.environ['PASSO_CAPTURE_USER']),st.query_params.get('a','105')),'value':(int(st.query_params.get('a','105')),'확인하는 사람'),'at':float('inf'),'members':[{'user_id':int(os.environ['PASSO_CAPTURE_USER']),'role':'확인하는 사람'}]}
st.session_state.setdefault('page','홈')
code=(ROOT/'app.py').read_text().replace('uid = None if demo else me()',"uid = int(os.environ['PASSO_CAPTURE_USER'])")
exec(compile(code,str(ROOT/'app.py'),'exec'),{'__name__':'__main__','__file__':str(ROOT/'app.py')})
