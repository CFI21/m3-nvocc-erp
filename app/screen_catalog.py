from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .json_recovery import load_json_or_recover_arrays
from .admin import ADMIN_SCREENS
from .masterdata import DOMAIN_SPECS, SCREENS as MASTER_SCREENS
from .clx044_gl_exact_flow import apply_gl_exact_flow

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
# These entries reference existing accepted screens; they do not increase the 196-screen catalog.
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
    {'label':'Template / Document Setup','target':'master-data::document-types'},
    {'label':'Configuration','target':'master-data::configurations'},
    {'label':'User Management','target':'administration::users','roles':['ADMIN','SECURITY_ADMIN','AUDITOR']},
    {'label':'Security Policy','target':'integration-security::security-policies','roles':['ADMIN','SECURITY_ADMIN','AUDITOR']},
    {'label':'Password Policy','target':'administration::password-policy','roles':['ADMIN','SECURITY_ADMIN','AUDITOR']},
    {'label':'MFA Policy','target':'administration::mfa-policy','roles':['ADMIN','SECURITY_ADMIN','AUDITOR']},
    {'label':'Data Scope / Policy','target':'administration::data-scope-rules','roles':['ADMIN','SECURITY_ADMIN','AUDITOR']},
    {'label':'Fiscal Year','target':'gl-accounts::fiscal-year'},
    {'label':'Detention Collection','target':'agent-tasks::detention-collection'},
    {'label':'Storage Cost','target':'agent-tasks::storage-cost'},
    {'label':'Approval Limits','target':'administration::approval-limits','roles':['ADMIN','FINANCE','GL_MANAGER','AUDITOR']},
    {'label':'Approval Queue','target':'master-data::approval-queue','roles':['ADMIN','MASTER_DATA_MANAGER','AUDITOR']},
    {'label':'Voucher Viewer','target':'gl-accounts::voucher-history'},
    {'label':'Audit History','target':'administration::audit','roles':['ADMIN','SECURITY_ADMIN','AUDITOR']},
    {'label':'Integration Audit','target':'integration-security::request-response-audit','roles':['ADMIN','SECURITY_ADMIN','AUDITOR']},
]


# Current 196-screen catalog remains authoritative. These are navigation-only
# business-area aliases to existing screens, so no second model or duplicate page
# is introduced. The structure can accept additional verified targets later.
BUSINESS_NAV_ALIASES = {
    'Setup': [
        {'label':'Customer','target':'master-data::customers','search_terms':['customer','client']},
        {'label':'Common Parties','target':'master-data::common-partys','search_terms':['common party','party']},
        {'label':'Carrier / Shipping Line','target':'master-data::carriers','search_terms':['carrier','shipping line','line']},
        {'label':'Vessel','target':'master-data::vessels','search_terms':['vessel','ship']},
        {'label':'Voyage Registration','target':'master-data::voyages','search_terms':['voyage','voyage registration']},
        {'label':'Port','target':'master-data::ports','search_terms':['port','pol','pod','pot']},
        {'label':'Location','target':'master-data::locations','search_terms':['location','place']},
        {'label':'Commodity','target':'master-data::commoditys','search_terms':['commodity','cargo']},
        {'label':'Units','target':'master-data::units','search_terms':['unit','uom']},
        {'label':'Sales Person','target':'master-data::sales-persons','search_terms':['sales person','sales']},
        {'label':'Equipment Type','target':'master-data::equipment-types','search_terms':['equipment type','container type']},
        {'label':'Container Type','target':'master-data::container-types','search_terms':['container type','equipment']},
    ],
    'Transaction / Operations': [
        {'label':'Booking','target':'agent-tasks::booking','search_terms':['booking','bkg']},
        {'label':'Job Planning','target':'agent-tasks::planning','search_terms':['job','job planning','planning']},
        {'label':'CRO','target':'agent-tasks::cro','search_terms':['cro','container release order']},
        {'label':'CRT','target':'agent-tasks::crt','search_terms':['crt','container release transaction']},
        {'label':'Export CRT','target':'agent-tasks::export-crt','search_terms':['export','export crt']},
        {'label':'Import CRT','target':'agent-tasks::import-crt','search_terms':['import','import crt']},
        {'label':'Transshipment CRT','target':'agent-tasks::transshipment-crt','search_terms':['transshipment','ts','transshipment crt']},
        {'label':'Container Activity','target':'agent-tasks::container-activity','search_terms':['container','container activity','equipment journey']},
        {'label':'Delivery Order','target':'agent-tasks::delivery-order','search_terms':['do','delivery order']},
        {'label':'Agent Receipt / Pay','target':'agent-tasks::agent-receipt-pay','search_terms':['agent receipt','agent pay','agent operations']},
    ],
    'Document': [
        {'label':'B/L','target':'agent-tasks::bl','search_terms':['b/l','bl','bill of lading','hbl','mbl']},
        {'label':'Switch B/L','target':'agent-tasks::switch-bl','search_terms':['switch bl','switch b/l']},
        {'label':'Split B/L','target':'agent-tasks::split-bl','search_terms':['split bl','split b/l']},
        {'label':'Import B/L','target':'agent-tasks::import-bl','search_terms':['import bl','import b/l']},
        {'label':'Release Instruction','target':'agent-tasks::delivery-order','search_terms':['release instruction','release','telex release']},
    ],
    'Equipment': [
        {'label':'Container Master / Activity','target':'agent-tasks::container-activity','search_terms':['container master','container','equipment']},
        {'label':'Detention Collection','target':'agent-tasks::detention-collection','search_terms':['detention','free days']},
        {'label':'Storage Cost','target':'agent-tasks::storage-cost','search_terms':['storage','demurrage']},
        {'label':'Equipment Type Setup','target':'master-data::equipment-types','search_terms':['equipment type','size type']},
        {'label':'Container Type Setup','target':'master-data::container-types','search_terms':['container type','iso type']},
    ],
    'Finance': [
        {'label':'Customer Invoice','target':'gl-accounts::invoice','search_terms':['invoice','sales invoice','ar']},
        {'label':'Vendor Bill / AP','target':'gl-accounts::bills','search_terms':['bill','ap','vendor bill','purchase invoice']},
        {'label':'Receipt','target':'gl-accounts::receipt','search_terms':['receipt','cash receipt']},
        {'label':'Payment','target':'gl-accounts::payment','search_terms':['payment','cash payment']},
        {'label':'SOA','target':'agent-tasks::soa','search_terms':['soa','statement of account']},
        {'label':'Profit & Loss','target':'gl-accounts::profit-loss','search_terms':['p&l','pnl','profit loss']},
        {'label':'Trial Balance','target':'gl-accounts::trial-balance-report','search_terms':['trial balance','tb']},
    ],
    'Control / Reporting': [
        {'label':'Approval Queue','target':'master-data::approval-queue','search_terms':['approval','approval queue']},
        {'label':'Master Audit','target':'master-data::audit','search_terms':['audit','master audit']},
        {'label':'Integration Audit','target':'integration-security::request-response-audit','search_terms':['integration audit','request response audit']},
        {'label':'Balance Sheet','target':'gl-accounts::balance-sheet','search_terms':['balance sheet','bs']},
        {'label':'GL Detail','target':'gl-accounts::gl-detail','search_terms':['gl detail','general ledger detail']},
        {'label':'Subledger / GL Reconciliation','target':'gl-accounts::subledger-gl-reconciliation','search_terms':['reconciliation','subledger reconciliation']},
    ],
}

SEARCH_SYNONYMS = {
    'booking':['booking','bkg'],
    'planning':['job','job planning','planning'],
    'cro':['cro','container release order'],
    'crt':['crt','container release transaction'],
    'export-crt':['export crt','export'],
    'import-crt':['import crt','import'],
    'transshipment-crt':['transshipment crt','transshipment','ts'],
    'bl':['b/l','bl','bill of lading','hbl','mbl'],
    'switch-bl':['switch bl','switch b/l'],
    'split-bl':['split bl','split b/l'],
    'import-bl':['import bl','import b/l'],
    'delivery-order':['do','delivery order','release instruction','release'],
    'container-activity':['container','container master','container activity','equipment journey'],
    'soa':['soa','statement of account'],
    'invoice':['invoice','sales invoice','ar'],
    'bills':['bill','vendor bill','purchase invoice','ap'],
    'receipt':['receipt','cash receipt'],
    'payment':['payment','cash payment'],
    'trial-balance-report':['trial balance','tb'],
    'profit-loss':['profit and loss','profit loss','p&l','pnl'],
    'balance-sheet':['balance sheet','bs'],
    'gl-detail':['gl detail','general ledger detail'],
    'subledger-gl-reconciliation':['reconciliation','subledger reconciliation'],
}


def _humanize(key: str) -> str:
    return key.replace('-', ' ').title()


def _load_json(path: str) -> dict[str, Any]:
    return json.loads((HERE / path).read_text())


def _base_screen(prefix: str, domain: str, submenu: str, key: str, name: str, route: str, actions: list[str], fields: list[str]|None=None, columns: list[str]|None=None) -> dict[str, Any]:
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
        'fields': list(fields or []),
        'columns': list(columns or []),
        'field_contract_api': f'/api/clx011/field-contract?screen_id={prefix}::{key}',
        'related_records_api': '/api/clx011/related/{job_ref}',
        'search_terms': list(SEARCH_SYNONYMS.get(key, [])),
        'extension_policy': {
            'field_extension': 'ADD_TO_EXISTING_SCREEN_FIRST',
            'section_extension': 'ADD_SECTION_OR_TAB_WHEN_SAME_BUSINESS_FUNCTION',
            'new_screen_extension': 'ONLY_FOR_VERIFIED_DISTINCT_BUSINESS_WORKSPACE',
        },
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


DOMAIN_SUBMENU_ORDER = {
    'Treasury / AR-AP': {
        'Overview': 0, 'Setup': 10, 'Cash / Bank': 20, 'Settlement': 30,
        'Payments & Release': 40, 'Cheques': 50, 'Work Queues': 70,
        'Reports & Reconciliation': 90,
    },
    'Integration & Security': {
        'Overview': 0, 'Providers': 10, 'Sandbox Flows': 20,
        'Event Control': 40, 'Reconciliation': 50, 'Security / Control': 60,
    },
    'General / Administration': {
        'Overview': 0, 'Organization': 10, 'Identity': 20,
        'Finance & Accounting Setup · Setup': 30,
        'Finance & Accounting Setup · Transaction': 40,
        'Finance & Accounting Setup · Reports': 45,
        'Governance': 50, 'Security': 60,
    },
    'Master Data': {'Master Records': 10, 'Governance': 20},
}

def _submenu_priority(domain: str, submenu: str) -> int:
    explicit=DOMAIN_SUBMENU_ORDER.get(domain,{})
    if submenu in explicit:return explicit[submenu]
    low=submenu.lower()
    if 'overview' in low or 'dashboard' in low:return 0
    if 'setup' in low or 'master' in low or 'organization' in low or 'identity' in low:return 15
    if 'transaction' in low or 'settlement' in low or 'payment' in low or 'cash' in low:return 35
    if 'control' in low or 'queue' in low or 'governance' in low or 'security' in low or 'reconciliation' in low:return 65
    if 'report' in low or 'audit' in low:return 90
    return 55

def _menu_screen_ids(domain_screens: list[dict[str, Any]], submenu: str) -> list[str]:
    xs=[s for s in domain_screens if s['submenu']==submenu]
    # Keep accepted business order, but audit/history viewers always finish the submenu.
    xs=sorted(enumerate(xs), key=lambda p: (
        1 if ('audit' in p[1]['name'].lower() or 'audit' in p[1]['key'].lower()) else 0,
        p[0]
    ))
    return [s['screen_id'] for _,s in xs]

def build_catalog() -> dict[str, Any]:
    screens: list[dict[str, Any]] = []

    # Agent Tasks: authoritative complete CLX module metadata.
    agent=_load_json('module_meta.json')['modules']
    for m in agent:
        screens.append(_base_screen(
            'agent-tasks','Agent Tasks','Transactions',m['key'],m['name'],m['route'],
            COMMON_MUTATE + COMMON_READ + ['approve','hold','cancel','amend','reissue','release','advance'],
            m.get('fields',[]),m.get('columns',[])
        ))

    # GL: recover all complete entries from the truncated accepted file, then restore
    # only the persisted modules proven by the accepted PostgreSQL dataset/hardening API.
    gl,_=load_json_or_recover_arrays(HERE/'gl_meta.json',['setup','transactions','controls','reports'])
    gl=apply_gl_exact_flow(gl)
    gl_groups={
        'setup':'Setup','transactions':'Transaction',
        'controls':'Reports','reports':'Reports'
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
            COMMON_MUTATE + COMMON_READ + ['approve','release','reverse'],
            m.get('fields',[]),m.get('columns',[])
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
        s=_base_screen(
            'treasury','Treasury / AR-AP',_group_name(m.get('group','')),m['key'],m['name'],m['route'],
            COMMON_MUTATE + COMMON_READ + ['approve','release','reverse'],
            m.get('fields',[]),m.get('columns',[])
        )
        # CLX-050: keep screen visibility aligned with the already-authoritative
        # Treasury API role matrix; do not grant any capability the API does not own.
        s['roles']=['ADMIN','FINANCE','GL_MANAGER','TREASURY_MANAGER','TREASURY','AR_ACCOUNTANT','AP_ACCOUNTANT','GL_ACCOUNTANT','AUDITOR','VIEWER','OPS']
        screens.append(s)

    # Integration & Security: complete accepted metadata.
    integration=_load_json('integration_meta.json')['modules']
    for m in integration:
        screens.append(_base_screen(
            'integration-security','Integration & Security',_group_name(m.get('group','')),m['key'],m['name'],m['route'],
            COMMON_READ + ['retry','activate','deactivate'],
            ['External Ref','Job Ref','Office Scope','Country Scope','Status','Version','Module Fields'],
            ['External Ref','Job Ref','Office Scope','Country Scope','Status']
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
            COMMON_READ + ['activate','deactivate','change-request','approve','reject','version-history'],
            ['Record ID','Reference','Name / Value','Status','Scope','Effective / Validity','Audit'],
            ['Reference','Name / Value','Status','Scope']
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
            COMMON_READ + ['change-request','approve','reject','activate','deactivate','version-history'],
            (['Record Key','Name','Status','Effective From','Effective To','Version','Module Fields'] if not is_governance else ['Reference','Domain','Status','Maker','Checker','Reason','Created At','Updated At']),
            (['Record Key','Name','Status','Effective From','Effective To'] if not is_governance else ['Reference','Domain','Status','Created At'])
        ))

    # Stable menu derived from the rebuilt catalog.
    # CLX-031 adds HO Tasks as navigation aliases only. Alias targets must already exist.
    screen_ids={s['screen_id'] for s in screens}
    for item in AGENT_SETUP_ALIASES + HO_TRANSACTION_ALIASES + HO_UTILITY_ALIASES:
        if item['target'] not in screen_ids:
            raise RuntimeError(f"M3_HO_ALIAS_TARGET_MISSING:{item['label']}->{item['target']}")
    for items in BUSINESS_NAV_ALIASES.values():
        for item in items:
            if item['target'] not in screen_ids:
                raise RuntimeError(f"M3_BUSINESS_NAV_TARGET_MISSING:{item['label']}->{item['target']}")
    # CLX-078 final top-level business navigation order:
    # HO control first, then Agent execution, followed by finance/integration/admin/master data.
    agent_screens=[s for s in screens if s['domain']=='Agent Tasks']
    menu=[{
        'domain':'M3 Business',
        'submenus':[
            {'name':name,'screens':[],'items':items}
            for name,items in BUSINESS_NAV_ALIASES.items()
        ],
        'screen_count':0,
        'navigation_alias_count':sum(len(v) for v in BUSINESS_NAV_ALIASES.values()),
        'navigation_only':True,
        'extensible':True,
    },{
        'domain':'HO Tasks',
        'submenus':[
            {'name':'Transaction','screens':[],'items':HO_TRANSACTION_ALIASES},
            {'name':'Utilities','screens':[],'items':HO_UTILITY_ALIASES},
        ],
        'screen_count':0,
        'navigation_alias_count':len(HO_TRANSACTION_ALIASES)+len(HO_UTILITY_ALIASES),
        'navigation_only':True,
    },{
        'domain':'Agent Tasks',
        'submenus':[
            {'name':'Setup','screens':[],'items':AGENT_SETUP_ALIASES},
            {'name':'Transaction','screens':[s['screen_id'] for s in agent_screens]},
        ],
        'screen_count':len(agent_screens),
        'navigation_alias_count':len(AGENT_SETUP_ALIASES),
        'navigation_only':False,
    }]

    domain_order=['Treasury / AR-AP','Integration & Security','General / Administration','Master Data']
    for domain in domain_order:
        domain_screens=[s for s in screens if s['domain']==domain]
        submenus=[]
        seen_sub=[]
        for s in domain_screens:
            if s['submenu'] not in seen_sub:
                seen_sub.append(s['submenu'])
        seen_sub=sorted(seen_sub,key=lambda sub:(_submenu_priority(domain,sub),seen_sub.index(sub)))
        for sub in seen_sub:
            ids=_menu_screen_ids(domain_screens,sub)
            submenus.append({'name':sub,'screens':ids})
        menu.append({'domain':domain,'submenus':submenus,'screen_count':len(domain_screens)})

    catalog={
        'project':'M3 NVOCC ERP',
        'baseline':'M3-CLX011-SCREEN-INTEGRATION-ACCEPTED-20260924-013SI',
        'parent':'M3-CLX010-ACCEPTED-20260924-012F',
        'screen_count':len(screens),
        'baseline_screen_count':196,
        'extension_count':max(0,len(screens)-196),
        'screen_count_policy':'196 is the current authoritative baseline, not a permanent hard limit; verified distinct workspaces may extend the count.',
        'extension_governance':{
            'field':'existing screen first; additive storage only when authoritative value cannot be derived',
            'section_tab':'extend existing screen when the business function is unchanged',
            'screen':'new screen only for a verified distinct business workspace with menu, role, audit, flow and tests',
            'placeholder_screens_forbidden':True,
            'duplicate_models_forbidden':True,
        },
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
    if len(screens) < 196:
        raise RuntimeError(f'M3_SCREEN_CATALOG_BELOW_ACCEPTED_BASELINE:{len(screens)}<196')
    return catalog
