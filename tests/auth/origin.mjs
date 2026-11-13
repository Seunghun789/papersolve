import assert from 'node:assert/strict';
import {sameOrigin} from '../../apps/web/lib/origin.ts';
const request=(origin,host='127.0.0.1:3000',proto)=>new Request('http://localhost:3000/api/test',{headers:{host,...(origin?{origin}:{}),...(proto?{'x-forwarded-proto':proto}:{})}});
assert.equal(sameOrigin(request('http://127.0.0.1:3000')),true);
assert.equal(sameOrigin(request('https://passo-dev.vercel.app','passo-dev.vercel.app','https')),true);
for(const origin of [undefined,'null','https://evil.example','http://127.0.0.1:3001','https://127.0.0.1:3000'])assert.equal(sameOrigin(request(origin)),false);
console.log('Origin checks: 7 passed');
