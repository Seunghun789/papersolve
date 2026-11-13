"""Run separately: python -m backend.worker. PostgreSQL leases survive process death."""
import argparse
import hashlib
import json
import os
import signal
import threading
import time

import httpx


class Worker:
    def __init__(self):
        self.url = os.environ['SUPABASE_URL'].rstrip('/')
        key = os.environ['SUPABASE_WORKER_SECRET_KEY']
        self.client = httpx.Client(timeout=45,trust_env=False,headers={'apikey':key,'Authorization':'Bearer '+key})

    def request(self, method, path, **kw):
        r = self.client.request(method,self.url+path,**kw)
        if not r.is_success:
            raise RuntimeError('worker_gateway_'+str(r.status_code))
        return r

    def rpc(self, name, body=None):
        return self.request('POST','/rest/v1/rpc/'+name,json=body or {}).json()

    def process(self, job):
        from backend.extraction import run_ocr, run_model, make_payload
        stop, lost = threading.Event(), threading.Event()
        args = {'jid':job['id'],'fence':job['lease_token']}
        def heartbeat():
            while not stop.wait(25):
                try:
                    if not self.rpc('passo_worker_save',args):
                        lost.set(); return
                except Exception:
                    lost.set(); return
        thread = threading.Thread(target=heartbeat,daemon=True);thread.start()
        def save(state):
            if lost.is_set() or not self.rpc('passo_worker_save',{**args,'checkpoint':state}):
                raise RuntimeError('lease_lost')
        try:
            source = self.request('GET','/rest/v1/source_inputs',params={'id':f"eq.{job['source_input_id']}",
                'tenant_id':f"eq.{job['tenant_id']}",'select':'storage_path'}).json()[0]
            content = self.request('GET','/storage/v1/object/authenticated/passo-originals/'+source['storage_path']).content
            if hashlib.sha256(content).hexdigest()!=job['sha256'] or len(content)!=job['byte_size']:
                raise ValueError('original_hash_mismatch')
            from backend.documents import validate_document
            validate_document(content,job['media_type'])
            rows = self.request('GET','/rest/v1/passo_checkpoints',params={'job_id':f"eq.{job['id']}",
                'tenant_id':f"eq.{job['tenant_id']}",'order':'revision.desc','limit':1,'select':'state'}).json()
            state = rows[0]['state'] if rows else {}
            if 'ocr' not in state:
                state['ocr'] = run_ocr(content,job['media_type']);save(state)
            if 'extraction' not in state:
                state['extraction'] = run_model(content,job['media_type']);save(state)
            payload = make_payload(state['extraction']['sheet'],state['ocr'])
            if lost.is_set() or not self.rpc('passo_worker_finish',{**args,'payload':payload}):
                raise RuntimeError('lease_lost')
            print(json.dumps({'job':job['id'],'status':'awaiting_review','records':len(payload['records'])}),flush=True)
        except Exception as exc:
            # No model output, document content, credentials or transport exceptions in logs.
            print(json.dumps({'job':job['id'],'error_type':type(exc).__name__}),flush=True)
            self.rpc('passo_worker_fail',args)
        finally:
            stop.set();thread.join(timeout=50)

    def close(self):
        self.client.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--once',action='store_true');args=parser.parse_args()
    stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):
        signal.signal(sig,lambda *_:stop.set())
    w=Worker()
    try:
        while not stop.is_set():
            jobs=w.rpc('passo_claim_document')
            if jobs: w.process(jobs[0])
            if args.once: break
            if not jobs: stop.wait(3)
    finally: w.close()

if __name__=='__main__':main()
