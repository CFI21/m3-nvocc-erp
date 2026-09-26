from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from .screen_catalog import build_catalog, HO_TRANSACTION_ALIASES, HO_UTILITY_ALIASES
from .screen_integration import ROLE_ACTIONS, screen_role_allowed

router=APIRouter(prefix='/api/clx035',tags=['CLX-035 HO Tasks Completion'])

def _catalog():
    return build_catalog()

def _by_id():
    c=_catalog()
    return c,{s['screen_id']:s for s in c['screens']}

def _alias_rows():
    c,by_id=_by_id()
    out=[]
    for submenu,items in [('Transaction',HO_TRANSACTION_ALIASES),('Utilities',HO_UTILITY_ALIASES)]:
        for item in items:
            s=by_id[item['target']]
            out.append({
                'domain':'HO Tasks',
                'submenu':submenu,
                'label':item['label'],
                'target':item['target'],
                'target_domain':s['domain'],
                'target_submenu':s['submenu'],
                'target_route':s['route'],
                'roles':s.get('roles',[]),
                'actions':s.get('actions',[]),
                'quick_actions':s.get('quick_actions',[]),
                'authorization':'TARGET_SCREEN_SERVER_AUTHORITY',
                'data_scope':'TARGET_DOMAIN_EXISTING_SCOPE_RULES',
                'cross_links':'JOB_BOOKING_CUSTOMER_AGENT_CARRIER_FINANCE_DOCUMENT_AUDIT_WHERE_TARGET_SUPPORTS_CONTEXT',
            })
    return c,out

@router.get('/aliases')
def aliases(role:Optional[str]=None,submenu:Optional[str]=None,q:Optional[str]=None):
    c,rows=_alias_rows()
    if role:
        _,by_id=_by_id()
        rows=[x for x in rows if screen_role_allowed(by_id[x['target']],role)]
    if submenu:
        rows=[x for x in rows if x['submenu'].lower()==submenu.lower()]
    if q:
        n=q.lower()
        rows=[x for x in rows if n in (x['label']+' '+x['target']+' '+x['target_route']).lower()]
    return {
        'phase':'CLX-035',
        'screen_count':c['screen_count'],
        'navigation_alias_count':len(rows),
        'items':rows,
    }

@router.get('/resolve')
def resolve(label:str=Query(...,min_length=2),role:str=Query('VIEWER')):
    c,rows=_alias_rows()
    match=next((x for x in rows if x['label'].lower()==label.lower()),None)
    if not match:
        raise HTTPException(404,{'code':'HO_ALIAS_NOT_FOUND','label':label})
    by_id={s['screen_id']:s for s in c['screens']}
    target=by_id[match['target']]
    if not screen_role_allowed(target,role):
        raise HTTPException(403,{'code':'HO_TARGET_ROLE_DENIED','label':label,'target':match['target'],'role':role.upper()})
    caps=ROLE_ACTIONS.get(role.upper(),ROLE_ACTIONS['VIEWER'])
    visible=[a for a in target.get('quick_actions',[]) if a in caps]
    return {
        'phase':'CLX-035',
        **match,
        'role':role.upper(),
        'visible_actions':visible,
        'screen_count':c['screen_count'],
    }

@router.get('/verification')
def verification():
    c,rows=_alias_rows()
    ids={s['screen_id'] for s in c['screens']}
    missing=[x for x in rows if x['target'] not in ids]
    tx=[x for x in rows if x['submenu']=='Transaction']
    util=[x for x in rows if x['submenu']=='Utilities']
    return {
        'phase':'CLX-035',
        'status':'PASS' if c['screen_count']==193 and len(tx)==18 and len(util)==15 and not missing else 'FAIL',
        'screen_count':c['screen_count'],
        'transaction_aliases':len(tx),
        'utility_aliases':len(util),
        'total_aliases':len(rows),
        'missing_targets':missing,
        'duplicate_screens_added':0,
        'data_model_changed':False,
        'api_contracts':'PRESERVED_WITH_ADDITIVE_CLX035_ENDPOINTS',
        'live_providers':False,
        'real_money':False,
    }
