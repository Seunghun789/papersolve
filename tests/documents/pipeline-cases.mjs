export async function pipelineCases({db,as,denied,check,uid}) {
 const reserve=(n=1,key=uid(900),digest='a'.repeat(64),tid=1)=>as(n,
  'select (public.passo_reserve_document($1,$2,$3,$4,$5,$6)).*',[tid,key,digest,'image/png',120,'probe.png']);
 await denied('viewer cannot reserve upload',()=>reserve(3));
 await denied('cross tenant reserve denied',()=>reserve(1,uid(900),'a'.repeat(64),2));
 const j=(await reserve())[0];
 check('new upload is not claimable before bytes exist',j.status==='uploading');
 check('same request returns same job',(await reserve())[0].id===j.id);
 await denied('same request with different bytes conflicts',()=>reserve(1,uid(900),'b'.repeat(64)));
 await denied('cannot queue absent original',()=>as(1,'select public.passo_enqueue_document(1,$1)',[j.id]));
 const path=(await db.query('select storage_path from source_inputs where id=$1',[j.source_input_id])).rows[0].storage_path;
 await as(1,"insert into storage.objects(bucket_id,name) values('passo-originals',$1)",[path]);
 await as(1,'select public.passo_enqueue_document(1,$1)',[j.id]);
 await denied('user cannot claim worker jobs',()=>as(1,'select * from public.passo_claim_document()'));
 await denied('user cannot read worker fences',()=>as(1,'select lease_token from public.passo_jobs'));
 const claim=async()=> (await db.query('select * from public.passo_claim_document()')).rows;
 const first=(await claim())[0];
 check('queued job leased once',first.id===j.id && first.attempts===1 && (await claim()).length===0);
 const save=async(fence,state=null)=>(await db.query('select public.passo_worker_save($1,$2,$3) ok',[j.id,fence,state])).rows[0].ok;
 check('checkpoint saved',await save(first.lease_token,{ocr:{'1':[]},extraction:{marker:true}}));
 await db.query("update passo_jobs set lease_until=now()-interval '1 second' where id=$1",[j.id]);
 const second=(await claim())[0];
 check('expired worker lease reclaimed',second.attempts===2 && first.lease_token!==second.lease_token);
 check('old worker cannot heartbeat or checkpoint',!await save(first.lease_token,{bad:true}));
 const state=(await db.query('select state from passo_checkpoints where job_id=$1',[j.id])).rows;
 check('checkpoint survives worker death',state.length===1 && state[0].state.extraction.marker);
 const payload={records:[{type_name:'probe',occurred_at:'2026-11-13',data:{quantity:{version:'cell-state-v1',raw:'-',value:null,status:'explicit_dash'}}}],spans:{quantity:[{rec:0,page:1,box:[1,2,3,4]}]}};
 const finish=async(fence,p=payload)=>(await db.query('select public.passo_worker_finish($1,$2,$3) ok',[j.id,fence,p])).rows[0].ok;
 check('stale worker cannot publish',!await finish(first.lease_token));
 try {await finish(second.lease_token,{...payload,records:[payload.records[0],{...payload.records[0],occurred_at:'invalid'}]});throw Error('expected rollback');}catch(e){if(e.code!=='22007')throw e;}
 check('partial record insert rolls back',(await db.query('select id from records where source_input_id=$1',[j.source_input_id])).rows.length===0);
 check('records and evidence committed atomically',await finish(second.lease_token));
 check('duplicate worker completion has no effect',!await finish(second.lease_token));
 check('exactly one row stored',(await db.query('select id from records where source_input_id=$1',[j.source_input_id])).rows.length===1);
 await denied('uploader cannot confirm',()=>as(5,'select public.passo_confirm_document(1,$1)',[j.id]));
 await denied('foreign tenant cannot confirm',()=>as(2,'select public.passo_confirm_document(1,$1)',[j.id]));
 await as(1,'select public.passo_confirm_document(1,$1)',[j.id]);
 await as(1,'select public.passo_confirm_document(1,$1)',[j.id]);
 const r=(await db.query('select status,data from records where source_input_id=$1',[j.source_input_id])).rows[0];
 check('confirmation retry preserves dash and exactly one record',r.status==='확정'&&r.data.quantity.value===null);
 // Remove only this fixture so original authorization suite retains its original counts.
 await db.query('delete from source_inputs where id=$1',[j.source_input_id]);
}
