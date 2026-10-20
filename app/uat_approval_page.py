"""Isolated read-only browser shell; Master Data mutations use authenticated APIs."""
import os
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from . import db

router=APIRouter()
@router.get("/api/admin/auth/uat-masterdata-approval-page",include_in_schema=False)
def uat_approval_page():
    if (os.getenv("M3_PRODUCTION_TRAFFIC","OFF").upper()!="OFF"
        or os.getenv("M3_UAT_PRESERVE_DATA")!="1"
        or not db.using_postgres() or not db._uat_target_approved()
        or urlparse(db.DATABASE_URL).hostname!="ep-wild-sea-b49pzkl3-pooler.c-6.us-east-2.aws.neon.tech"):
        raise HTTPException(404,{"code":"NOT_FOUND"})
    html='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>M3 Isolated Maker Checker Approval UAT</title><style>body{font:15px system-ui;max-width:720px;margin:20px auto;padding:12px;color:#172033}main{padding:22px;border:1px solid #ccd;border-radius:10px}label{display:block;margin:12px 0}input,select,button{font:inherit;padding:8px;max-width:100%;box-sizing:border-box}input{width:100%}button{margin:6px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f5fa;padding:12px}section{border-top:1px solid #ddd;margin-top:18px;padding-top:12px}</style></head><body><main>
<h2>Isolated Master Data approval UAT</h2><p>Only synthetic unit records with keys starting UAT-BROWSER-. Maker and Checker sign in separately. Sessions stay in browser memory; never share passwords or tokens.</p>
<label>UAT access token <input id="uat" type="password" autocomplete="off"></label>
<section><h3>Maker login</h3><label>Password <input id="makerPassword" type="password" autocomplete="off"></label><label>MFA code <input id="makerMfa" autocomplete="off"></label><button id="makerLogin">Sign in Maker</button><h3>Checker login</h3><label>Password <input id="checkerPassword" type="password" autocomplete="off"></label><label>MFA code <input id="checkerMfa" autocomplete="off"></label><button id="checkerLogin">Sign in Checker</button><button id="logout">End both sessions</button><p id="authStatus">Not authenticated</p></section>
<section><h3>1. Create synthetic change as Maker</h3><button id="create">Submit new unique UAT unit</button></section>
<section><h3>2. Independent Checker decision</h3><label>Change reference <input id="ref" placeholder="MDC-..."></label><button id="approve">Approve</button><button id="reject">Reject</button><button id="withdraw">Withdraw as Maker</button></section>
<section><h3>B01 Agent Office duplicate prevention</h3><p>Read-only outcome check against existing synthetic Agent UAT-AGENT-001 and office RTM. This test expects HTTP 409 and must not create a change.</p><button id="duplicate">Test Duplicate Prevention</button></section>
<section><h3>3. Inspect state</h3><button id="review">Read change and unit record</button><pre id="result" role="status">No action performed</pre></section>
</main><script>
const $=id=>document.getElementById(id);
const sessions={maker:'',checker:''};
function showAuth(){$('authStatus').textContent='Maker: '+(sessions.maker?'ready':'not signed in')+'; Checker: '+(sessions.checker?'ready':'not signed in');}
async function logoutRole(role){const token=sessions[role];sessions[role]='';if(token)await fetch('/api/admin/auth/logout',{method:'POST',headers:{'X-M3-UAT-Token':$('uat').value,'X-M3-Session':token},credentials:'omit',cache:'no-store'}).catch(()=>{});}
async function loginRole(role){const password=$(role+'Password'),mfa=$(role+'Mfa');try{if(!$('uat').value||!password.value||!mfa.value)throw Error('UAT token, password and MFA required');await logoutRole(role);const username=role==='maker'?'md.maker':'md.checker';const response=await fetch('/api/admin/auth/login',{method:'POST',headers:{'X-M3-UAT-Token':$('uat').value,'Content-Type':'application/json'},credentials:'omit',cache:'no-store',body:JSON.stringify({username,password:password.value,mfa_code:mfa.value})});const login=await response.json().catch(()=>({}));if(!response.ok||!login.session_token||login.mfa_verified!==true)throw Error('Login or MFA failed (HTTP '+response.status+')');let token=login.session_token;const valid=login.username===username&&Array.isArray(login.roles)&&login.roles.includes(role==='maker'?'MASTER_DATA':'MASTER_DATA_MANAGER')&&!login.roles.includes('SUPER_ADMIN');if(!valid){await fetch('/api/admin/auth/logout',{method:'POST',headers:{'X-M3-UAT-Token':$('uat').value,'X-M3-Session':token},credentials:'omit'}).catch(()=>{});throw Error('Role isolation check failed');}sessions[role]=token;$('result').textContent=role+' authentication: PASS';}catch(e){$('result').textContent=role+' authentication: '+e.message;}finally{password.value='';mfa.value='';showAuth();}}
$('makerLogin').onclick=()=>loginRole('maker');
$('checkerLogin').onclick=()=>loginRole('checker');
$('logout').onclick=async()=>{await logoutRole('maker');await logoutRole('checker');showAuth();$('result').textContent='Sessions ended';};

let lastKey='';
function headers(role){return {'X-M3-UAT-Token':$('uat').value,'X-M3-Session':sessions[role],'Content-Type':'application/json'};}
async function request(path,role,method='GET',body){if(!$('uat').value||!sessions[role])throw Error('UAT and '+role+' session tokens required');const res=await fetch(path,{method,headers:headers(role),body:body?JSON.stringify(body):undefined,credentials:'omit',cache:'no-store'});const data=await res.json().catch(()=>({}));if(!res.ok)throw Error('HTTP '+res.status+': '+JSON.stringify(data));return data;}
async function run(task){for(const b of document.querySelectorAll('button'))b.disabled=true;try{$('result').textContent=JSON.stringify(await task(),null,2);}catch(e){$('result').textContent='UAT not completed: '+e.message;}finally{for(const b of document.querySelectorAll('button'))b.disabled=false;}}
$('create').onclick=()=>run(async()=>{const key='UAT-BROWSER-'+crypto.randomUUID().slice(0,12).toUpperCase();const x=await request('/api/masterdata/changes','maker','POST',{domain:'unit',record_key:key,operation:'CREATE',payload:{name:key},reason:'Isolated browser Maker Checker UAT'});lastKey=key;$('ref').value=x.change_ref;return {record_key:key,...x};});
async function assertSynthetic(ref){if(!/^MDC-[A-Z0-9]+$/.test(ref))throw Error('Valid change ref required');const all=await request('/api/masterdata/changes','checker');const ch=all.find(x=>x.change_ref===ref);if(!ch||ch.domain!=='unit'||!String(ch.record_key||'').startsWith('UAT-BROWSER-'))throw Error('Only synthetic UAT-BROWSER- unit changes are permitted in this page');if(ch.status!=='PENDING')throw Error('Change is not pending');return ch;}
async function decide(action){const ref=$('ref').value.trim();await assertSynthetic(ref);return await request('/api/masterdata/changes/'+encodeURIComponent(ref)+'/decision','checker','POST',{decision:action,comment:'Isolated UAT '+action});}
$('approve').onclick=()=>run(()=>decide('APPROVE'));
$('reject').onclick=()=>run(()=>decide('REJECT'));
$('withdraw').onclick=()=>run(async()=>{const ref=$('ref').value.trim();await assertSynthetic(ref);return request('/api/masterdata/changes/'+encodeURIComponent(ref)+'/withdraw','maker','POST',{reason:'Synthetic UAT withdrawal'});});
$('review').onclick=()=>run(async()=>{const changes=await request('/api/masterdata/changes','checker');const ref=$('ref').value.trim();const matching=changes.find(x=>x.change_ref===ref);const units=await request('/api/masterdata/records/unit','checker');return {change:matching||null,record:lastKey?units.find(x=>x.record_key===lastKey)||null:null};});
$('duplicate').onclick=()=>run(async()=>{const agent='UAT-AGENT-001',office='RTM';const before=await request('/api/masterdata/changes','maker');const key='AGCFG-UATDUP-'+crypto.randomUUID().slice(0,10).toUpperCase();const payload={domain:'configuration',record_key:key,operation:'CREATE',payload:{config_type:'AGENT_OFFICE_ASSIGNMENT',agent_code:agent,office_code:office,address:'B01 isolated duplicate safety test'},reason:'B01 UAT duplicate rejection expected'};const response=await fetch('/api/masterdata/changes',{method:'POST',headers:headers('maker'),body:JSON.stringify(payload),credentials:'omit',cache:'no-store'});const data=await response.json().catch(()=>({}));const after=await request('/api/masterdata/changes','maker');const created=after.some(x=>x.record_key===key);const passed=response.status===409&&data.detail?.code==='DUPLICATE_AGENT_RELATIONSHIP'&&!created&&after.length===before.length;return {test:'Agent Office duplicate prevention',status:passed?'PASS':'FAIL',http_status:response.status,error_code:data.detail?.code||null,record_created:created,change_count_before:before.length,change_count_after:after.length};});
</script></body></html>'''
    return HTMLResponse(html,headers={"Cache-Control":"no-store, max-age=0","X-Frame-Options":"DENY","Referrer-Policy":"no-referrer","Content-Security-Policy":"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"})
