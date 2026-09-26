from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .json_recovery import load_json_or_recover_arrays
from .admin import ADMIN_SCREENS
from .masterdata import DOMAIN_SPECS, SCREENS as MASTER_SCREENS

HERE = Path(__file__).resolve().parent

COMMON_READ = ['quick-view','related-records','print','export','email','audit-history']
COMMON_MUTATE = ['create','edit','copy']
COMMON_APPROVAL = ['approve','hold','cancel','amend','reissue','release','reverse']

# CLX-038: Agent Tasks starts with Setup masters, then operational Transactions.
AGENT_SETUP_ALIASES = [
    {'label':'Agent','target':'master-data::agents'},
    {'label':'Common Parties','target':'master-data::common-partys'},
    {'label':'Vessel','target':'master-data::vessels'},
    {'label':'Voyage Registration','target':'master-data::voyages'},
    {'label':'Commodity','target':'master-data::commoditys'},
    {'label':'Units','target':'master-data::units'},
    {'label':'Sales Person','target':'master-data::sales-persons'},
]

# CLX-031: legacy HO Tasks navigation aliases only.
# These entries reference existing accepted screens; they do not increase the 193-screen catalog.
HO_TRANSACTION_ALIASES = [
    {'label':'Agent Opening','target':'master-data::agents'},
    {'label':'Vendor Opening','target':'master-data::carriers'},
    {'label':'Customer Opening','target':'master-data::customers'},
    {'label':'Container Purchase','target':'gl-accounts::bills'},
    {'label':'Container Sale','target':'gl-accounts::invoice'},
    {'label':'Purchase Invoice','target':'gl-accounts::bills'},
    {'label':'Purchase Invoice Slot','target':'gl-accounts::bills'},
    {'label':'Sale Invoice','target':'gl-accounts::invoice'},
    {'label':'Payment','target':'gl-accounts::payment'},
    {'label':'Receipt','target':'gl-accounts::receipt'},
    {'label':'Purchase Invoice Storage','target':'agent-tasks::storage-cost'},
    {'label':'Sale Invoice Detention','target':'agent-tasks::detention-collection'},
    {'label':'Sale Invoice Import','target':'gl-accounts::invoice'},
    {'label':'Lease Rental Transaction','target':'gl-accounts::prepayments'},
    {'label':'Container Exchange','target':'agent-tasks::container-activity'},
    {'label':'Exchange Rate Update','target':'integration-security::fx-rate-feed'},
    {'label':'Third Party Deal Info','target':'integration-security::provider-adapters'},
    {'label':'Third Party Tracking Info','target':'integration-security::request-response-audit'},
]

HO_UTILITY_ALIASES = [
    {'label':'User Management','target':'administration::users','roles':['ADMIN','SECURITY_ADMIN','AUDITOR']},
    {'label':'Security Policy','target':'integration-security::security-policies','roles':['ADMIN','SECURITY_ADMIN','AUDITOR']},
    {'label':'Password Policy','target':'administration::password-policy','roles':['ADMIN','SECURITY_ADMIN','AUDITOR']},
    {'label':'MFA Policy','target':'administration::mfa-policy','roles':['ADMIN','SECURITY_ADMIN','AUDITOR']},
    {'label':'Data Scope / Policy','target':'administration::data-scope-rules','roles':['ADMIN','SECURITY_ADMIN','AUDITOR']},
    {'label':'Detention Collection','target':'agent-tasks::detention-collection'},
    {'label':'Storage Cost','target':'agent-tasks::storage-cost'},
    {'label':'Template / Document Setup','target':'master-data::document-types'},
    {'label':'Configuration','target':'master-data::configurations'},
    {'label':'Audit History','target':'administration::audit','roles':['ADMIN','SECURITY_ADMIN','AUDITOR']},
    {'label':'Integration Audit','target':'integration-security::request-response-audit','roles':['ADMIN','SECURITY_ADMIN','AUDITOR']},
    {'label':'Approval Limits','target':'administration::approval-limits','roles':['ADMIN','FINANCE','GL_MANAGER','AUDITOR']},
    {'label':'Approval Queue','target':'master-data::approval-queue','roles':['ADMIN','MASTER_DATA_MANAGER','AUDITOR']},
    {'label':'Fiscal Year','target':'gl-accounts::fiscal-year'},
    {'label':'Voucher Viewer','target':'gl-accounts::voucher-history'},
]


def _humanize(key: str) -> str:
    return key.replace('-', ' ').title()


def _load_json(path: str) -> dict[str, Any]:
    return json.loads((HERE / path).read_text())


def _base_screen(prefix: str, domain: str, submenu: str, key: str, name: str, route: str, actions: list[str]) -> dict[str, Any]:
    return {
        'screen_id': f'{prefix}::{key}',
        'domain': domain,
        'submenu': submenu,
        'key': key,
        'name': name,
        'route': route,
        'purpose': f'{name} within the accepted M3 NVOCC ERP workflow.',
        'actions': actions,
        'quick_actions': actions,
        'roles': ['ADMIN','OPS','DOCS','FINANCE','AGENT','VIEWER'],
        'audit': 'existing immutable domain audit + CLX-011 navigation/action intent audit',
    }


def _group_name(group: str) -> str:
    g=(group or '').strip().lower()
    names={
        'overview':'Overview',
        'setup':'Setup',
        'transactions':'Transactions',
        'controls':'Controls & Month End',
        'reports':'Reports & Reconciliation',
        'settlement':'Settlement',
        'payments':'Payments & Release',
        'cash-bank':'Cash / Bank',
        'cheques':'Cheques',
        'work-queues':'Work Queues',
        'providers':'Providers',
        'sandbox flows':'Sandbox Flows',
        'event control':'Event Control',
        'reconciliation':'Reconciliation',
        'security / control':'Security / Control',
        'identity':'Identity',
        'security':'Security',
        'organization':'Organization',
        'governance':'Governance',
        'master':'Master Records',
    }
    return names.get(g, _humanize(group) if group else 'General')


def build_catalog() -> dict[str, Any]:
    screens: list[dict[str, Any]] = []

    # Agent Tasks: authoritative complete CLX module metadata.
    agent=_load_json('module_meta.json')['modules']
    for m in agent:
        screens.append(_base_screen(
            'agent-tasks','Agent Tasks','Transactions',m['key'],m['name'],m['route'],
            COMMON_MUTATE + COMMON_READ + ['approve','hold','cancel','amend','reissue','release','advance']
        ))

    # GL: recover all complete entries from the truncated accepted file, then restore
    # only the persisted modules proven by the accepted PostgreSQL dataset/hardening API.
    gl,_=load_json_or_recover_arrays(HERE/'gl_meta.json',['setup','transactions','controls','reports'])
    gl_groups={
        'setup':'Setup','transactions':'Transactions',
        'controls':'Controls & Month End','reports':'Reports & Reconciliation'
    }
    gl_modules=[]
    for group in ('setup','transactions','controls','reports'):
        for m in gl.get(group,[]):
            gl_modules.append((group,m))
    gl_extra=[
        ('reports',{'key':'trial-balance-report','name':'Trial Balance','route':'/gl/reports/trial-balance'}),
        ('reports',{'key':'profit-loss','name':'Profit & Loss','route':'/gl/reports/profit-loss'}),
        ('reports',{'key':'balance-sheet','name':'Balance Sheet','route':'/gl/reports/balance-sheet'}),
        ('reports',{'key':'gl-detail','name':'GL Detail','route':'/gl/reports/gl-detail'}),
        ('reports',{'key':'subledger-gl-reconciliation','name':'Subledger / GL Reconciliation','route':'/gl/reports/subledger-gl-reconciliation'}),
    ]
    seen={m['key'] for _,m in gl_modules}
    gl_modules += [(g,m) for g,m in gl_extra if m['key'] not in seen]
    for group,m in gl_modules:
        s=_base_screen(
            'gl-accounts','General / Administration','Finance & Accounting Setup · '+gl_groups[group],m['key'],m['name'],m['route'],
            COMMON_MUTATE + COMMON_READ + ['approve','release','reverse']
        )
        s['roles']=['ADMIN','GL_MANAGER','GL_ACCOUNTANT','AUDITOR']
        screens.append(s)

    # Treasury: recover complete definitions and restore the one accepted persisted
    # module whose object was cut by the 1,000-line source truncation.
    treasury,_=load_json_or_recover_arrays(HERE/'treasury_meta.json',['modules'])
    treasury_modules=list(treasury.get('modules',[]))
    if 'bank-reconciliation-exception-queue' not in {m['key'] for m in treasury_modules}:
        treasury_modules.append({
            'key':'bank-reconciliation-exception-queue',
            'name':'Bank Reconciliation Exception Queue',
            'group':'work-queues',
            'route':'/treasury/work-queues/bank-reconciliation-exception-queue',
        })
    for m in treasury_modules:
        screens.append(_base_screen(
            'treasury','Treasury / AR-AP',_group_name(m.get('group','')),m['key'],m['name'],m['route'],
            COMMON_MUTATE + COMMON_READ + ['approve','release','reverse']
        ))

    # Integration & Security: complete accepted metadata.
    integration=_load_json('integration_meta.json')['modules']
    for m in integration:
        screens.append(_base_screen(
            'integration-security','Integration & Security',_group_name(m.get('group','')),m['key'],m['name'],m['route'],
            COMMON_READ + ['retry','activate','deactivate']
        ))

    # Administration: source code is authoritative for all 28 screens.
    admin_meta={m['key']:m for m in _load_json('admin_meta.json')['modules']}
    for key in ADMIN_SCREENS:
        m=admin_meta.get(key,{})
        group=m.get('group')
        if not group:
            if key in {'dashboard','users','user-status','roles','permission-matrix','role-assignments'}: group='identity'
            elif key in {'organizations','countries','legal-entities','offices','branches','departments','office-membership'}: group='organization'
            elif key in {'data-scope-rules','customer-agent-access','approval-limits','maker-checker','approval-delegations','temporary-access','access-reviews'}: group='governance'
            else: group='security'
        screens.append(_base_screen(
            'administration','General / Administration',_group_name(group),key,
            m.get('name',_humanize(key)),m.get('route',f'/admin/{group}/{key}'),
            COMMON_READ + ['activate','deactivate','change-request','approve','reject','version-history']
        ))

    # Master Data: source code is authoritative for 29 domains + 13 governance screens.
    master_names={}
    for domain,name in DOMAIN_SPECS.items():
        key=domain+'s' if not domain.endswith('s') else domain
        master_names[key]=name
    governance_names={
        'dashboard':'Master Data Dashboard',
        'change-requests':'Change Requests',
        'approval-queue':'Approval Queue',
        'versions':'Versions',
        'duplicate-review':'Duplicate Review',
        'aliases-merges':'Aliases / Merges',
        'data-quality':'Data Quality',
        'reference-usage':'Reference Usage',
        'effective-dates':'Effective Dates',
        'sequence-control':'Sequence Control',
        'integrity-scan':'Integrity Scan',
        'hardcoded-scan':'Hardcoded Scan',
        'audit':'Master Audit',
    }
    for key in MASTER_SCREENS:
        is_governance=key in governance_names
        name=governance_names.get(key,master_names.get(key,_humanize(key)))
        screens.append(_base_screen(
            'master-data','Master Data','Governance' if is_governance else 'Master Records',
            key,name,f'/master-data/{"governance" if is_governance else "records"}/{key}',
            COMMON_READ + ['change-request','approve','reject','activate','deactivate','version-history']
        ))

    # Stable menu derived from the rebuilt catalog.
    # CLX-031 adds HO Tasks as navigation aliases only. Alias targets must already exist.
    screen_ids={s['screen_id'] for s in screens}
    for item in AGENT_SETUP_ALIASES + HO_TRANSACTION_ALIASES + HO_UTILITY_ALIASES:
        if item['target'] not in screen_ids:
            raise RuntimeError(f"M3_HO_ALIAS_TARGET_MISSING:{item['label']}->{item['target']}")
    menu=[{
        'domain':'HO Tasks',
        'submenus':[
            {'name':'Transaction','screens':[],'items':HO_TRANSACTION_ALIASES},
            {'name':'Utilities','screens':[],'items':HO_UTILITY_ALIASES},
        ],
        'screen_count':0,
        'navigation_alias_count':len(HO_TRANSACTION_ALIASES)+len(HO_UTILITY_ALIASES),
        'navigation_only':True,
    }]
    # Agent Tasks: Setup first, all operational screens remain under Transaction.
    agent_screens=[s for s in screens if s['domain']=='Agent Tasks']
    menu.append({
        'domain':'Agent Tasks',
        'submenus':[
            {'name':'Setup','screens':[],'items':AGENT_SETUP_ALIASES},
            {'name':'Transaction','screens':[s['screen_id'] for s in agent_screens]},
        ],
        'screen_count':len(agent_screens),
        'navigation_alias_count':len(AGENT_SETUP_ALIASES),
        'navigation_only':False,
    })

    domain_order=['Treasury / AR-AP','Integration & Security','General / Administration','Master Data']
    for domain in domain_order:
        domain_screens=[s for s in screens if s['domain']==domain]
        submenus=[]
        seen_sub=[]
        for s in domain_screens:
            if s['submenu'] not in seen_sub:
                seen_sub.append(s['submenu'])
        for sub in seen_sub:
            ids=[s['screen_id'] for s in domain_screens if s['submenu']==sub]
            submenus.append({'name':sub,'screens':ids})
        menu.append({'domain':domain,'submenus':submenus,'screen_count':len(domain_screens)})

    catalog={
        'project':'M3 NVOCC ERP',
        'baseline':'M3-CLX011-SCREEN-INTEGRATION-ACCEPTED-20260924-013SI',
        'parent':'M3-CLX010-ACCEPTED-20260924-012F',
        'screen_count':len(screens),
        'screens':screens,
        'menu':menu,
        'catalog_recovery':'REBUILT_FROM_AUTHORITATIVE_SOURCE_METADATA_AND_ACCEPTED_PERSISTED_MODULES',
        'clx031_ho_tasks':{
            'navigation_only':True,
            'transaction_alias_count':len(HO_TRANSACTION_ALIASES),
            'utility_alias_count':len(HO_UTILITY_ALIASES),
            'screen_count_unchanged':True,
        },
    }
    if len(screens) != 193:
        raise RuntimeError(f'M3_SCREEN_CATALOG_COUNT_MISMATCH:{len(screens)}!=193')
    return catalog
