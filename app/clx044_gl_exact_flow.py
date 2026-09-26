from copy import deepcopy
from fastapi import APIRouter, HTTPException

GL_SETUP_ORDER=[
 'chart-of-accounts','voucher-properties','opening-balance','account-integration','currency'
]
GL_TRANSACTION_ORDER=[
 'voucher','invoice','bills','receipt','payment','bank-reconciliation','cheque-book-stock','budget',
 'cash-denomination-record','cheques-opening','reconciliation-date-setup','payment-requisition-setup',
 'payment-requisition','cheque-delivery-marking','cheque-deposit-marking','recall-memo-voucher',
 'voucher-approval-dashboard','voucher-history','wht-deposits','tax-tool'
]

FLOW={
 'chart-of-accounts':{
  'tabs':['Account Detail','Account Security'],
  'fields':['Parent Account','Account Code','Title of Account','Allow Voucher Entry','In-Active','Alias','Max Child Account','Category','Sub Category','P&L Category','REFERENCE NO.','User Defined Field 2','User Defined Field 3','User Defined Field 4','Company Assignment'],
  'columns':['Account Code','Title of Account','Parent Account','Category','Currency','Active'],
  'actions':['Account Movement','Upload','Credit Limit/Day Upload'],
  'tree_roots':['ASSETS','CAPITAL','LIABILITY','REVENUE','EXPENSE']
 },
 'voucher-properties':{
  'tabs':['CostCenter','Department','Location'],
  'fields':['Property Type','Short Code','Name','Remarks'],
  'columns':['Property Type','Code','Description','Remarks']
 },
 'opening-balance':{
  'fields':['Period','Account Code','Account Name','Debit','Credit','Currency','Status'],
  'columns':['Period','Account Code','Account Name','Debit','Credit','Currency','Status']
 },
 'account-integration':{
  'tabs':['Parent Account','General Parent Account','Export Common Account','Import Common Account','Logistics Common Account','Other Account'],
  'fields':['Applicable Date','Description','A/C Type Wise','Vendor City','Vendor A/c','Vendor All City A/c','Consignee City','Consignee A/c','Consignee All City A/c','Shipper City','Shipper A/c','Shipper All City A/c','Principal A/c','Terminal A/c','Commission Agent A/c','Overseas Agent A/c','Export Ocean Freight Revenue','Export Ocean Freight Expense','Export Documentation Revenue','Export Documentation Expense','Export LCL Revenue','Export LCL Expense','Export FCL Revenue','Export FCL Expense','Export AIR Revenue','Export AIR Expense','Export BreakBulk Revenue','Export BreakBulk Expense','Import Ocean Freight Revenue','Import Ocean Freight Expense','Import Delivery Order Revenue','Import Delivery Order Expense','Import LCL Revenue','Import LCL Expense','Import FCL Revenue','Import FCL Expense','Import AIR Revenue','Import AIR Expense','Import BreakBulk Revenue','Import BreakBulk Expense','Security Receivable','Security Payable','Logistics Revenue Account','Logistics Expense Account','Security InHand','WIP','Principal','Bank Charges Account','Convenience','Exchange Rate G/L','Advance Against Running Detention','Margin Account','Round Factor Account','Negative Round'],
  'columns':['Applicable Date','Description','Mapping Group','Account Code','Account Name','Active'],
  'actions':['Bulk Upload','Get Fields List','WIP Policy','View Advance']
 },
 'currency':{
  'fields':['Code','Name','Main Symbol','Sub Unit Symbol','Decimal Portion Digits','Default','No Sub Unit','Active'],
  'columns':['Code','Name','Main Symbol','Sub Unit Symbol','Decimal Portion Digits','Default','Active'],
  'actions':['Bulk Upload']
 },
 'voucher':{
  'tabs':['Account Detail','Voucher Properties'],
  'fields':['Voucher No.','Date','Voucher Type','Company','Settlement Account','Cost Center','Bank Sub Type','Currency','Exchange Rate','Cheque No.','Cheque Date','Pay To','Print On Letter Head','Audit Adjustment Entry','Extended Voucher','Account Lines','Receipt','Narration','Apply','Drawn At','Show Narration in Report','Status'],
  'columns':['Voucher No.','Date','Voucher Type','Company','Currency','Debit','Credit','Status'],
  'line_columns':['Account Code','Particular','Cost Center','Debit (VC)','Credit (VC)','Debit (LC)','Credit (LC)','Narration']
 },
 'invoice':{
  'tabs':['Invoice Detail','Voucher Properties','Invoice Properties'],
  'fields':['Voucher No.','Invoice No.','GST Invoice No.','Voucher / Inv Date','Due Date','Company','Balance','Customer / Supplier','Currency','Cost Center','Exchange Rate','Print On','Account Lines','Narration','Invoice Amount','Tax Amount','Net Amount','Status'],
  'columns':['Invoice No.','Voucher / Inv Date','Customer / Supplier','Currency','Invoice Amount','Tax Amount','Net Amount','Status'],
  'line_columns':['Account Code','Particular','Cost Centre','Dr/Cr','Amount VC','Amount LC','Narration','TaxType']
 },
 'bills':{
  'tabs':['Invoice Detail','Voucher Properties','Invoice Properties'],
  'fields':['Voucher No.','Bill No.','GST Invoice No.','Voucher / Bill Date','Vendor Bill Date','Due Date','Company','Balance','Vendor','Currency','Cost Center','Exchange Rate','Print On','Account Lines','Narration','Invoice Amount','Tax Amount','Net Amount','Status'],
  'columns':['Bill No.','Voucher / Bill Date','Vendor','Currency','Invoice Amount','Tax Amount','Net Amount','Status'],
  'line_columns':['Account Code','Particular','Cost Centre','Dr/Cr','Amount VC','Amount LC','Narration','TaxType']
 },
 'receipt':{
  'tabs':['Account Detail','Invoice','Settlement','Voucher Properties','Account Properties'],
  'fields':['Voucher No.','Date','Type','Cheque Type','Print On','Company','Settlement Account','Party Account','Receipt Amount','Cost Center','Bank Sub Type','Currency','Exchange Rate','Cheque No.','Cheque Date','Received From','Drawn At','Account Lines','Narration','Inv Adj Amount','Set Adj Amount','Net Amount','Status'],
  'columns':['Voucher No.','Date','Received From','Currency','Receipt Amount','Net Amount','Status'],
  'line_columns':['Account Code','Particular','Cost Centre','Dr/Cr','Amount VC','Amount LC','Narration']
 },
 'payment':{
  'tabs':['Account Detail','Invoice','Settlement','Voucher Properties','Account Properties'],
  'fields':['Voucher No.','Date','Type','Cheque Type','Print On','Company','Settlement Account','Party Account','Payment Amount','Cost Center','Bank Sub Type','Currency','Exchange Rate','Cheque No.','Cheque Date','Pay To','Drawn At','Filer Status','Nature','Tax Section','WHT %','Tax Account Code','Tax Account Name','Tax Deducted','Account Lines','Narration','Inv Adj Amount','Set Adj Amount','Net Amount','Status'],
  'columns':['Voucher No.','Date','Pay To','Currency','Payment Amount','WHT %','Net Amount','Status'],
  'line_columns':['Account Code','Particular','Cost Centre','Dr/Cr','Amount VC','Amount LC','Narration','Tax Deducted','WHT %']
 },
 'bank-reconciliation':{
  'tabs':['Record','Post Record'],
  'fields':['Company','Statement Date','Statement Balance','Exclude CR Voucher','Bank','Currency','Filter','Book Balance','UnReconciled Amount','Difference','Posting Date Basis','Other Date','Manual Post Date','Reconciliation Lines','Status'],
  'columns':['Post','Post Date','Chq No','Chq Date','Vch Date','Voucher No','Debit','Credit','Amount','Pay To / Party Name','CostCenter','Remarks','Last Posting Log By'],
  'actions':['Search','Pick','Apply Filter','Upload','Show Posted Record','Reconciled History']
 },
 'cheque-book-stock':{
  'fields':['Document No','Document Date','Company','Bank','Starting Cheque No','Ending Cheque No','Cheque No Prefix','Cheque No Max Length','Cheque Stock'],
  'columns':['S No','Status','Cheque No','Voucher No','Voucher Date','Amount','Narration','Remarks','Create By'],
  'actions':['Generate Cheque']
 },
 'budget':{
  'fields':['Company','Period','Date','Remarks','Current','Budget Lines'],
  'columns':['Enforcement','Account Code','Particular','Cost Center','Total(Amount)','Quarterly(Amount)','Monthly(Amount)','Manual'],
  'actions':['Copy','Show All']
 },
 'cash-denomination-record':{
  'fields':['Entry No','Date','Company','Cash Account','Cost Center','Currency','Balance','Remarks','5000','1000','500','100','50','20','10','5','2','1','Coins','Difference','Total'],
  'columns':['Entry No','Date','Company','Cash Account','Cost Center','Currency','Balance','Total','Difference'],
  'actions':['Pick']
 },
 'cheques-opening':{
  'fields':['Doc No','Transferred','Date','Company','Bank','Receipt / Payment','Cheque No','Cheque Date','Voucher To','Currency','Cost Center','Narration','Drawn Bank','Deposited','Deposited Date','Posted','Posted Date','Amount VC','Amount LC','Exchange Rate','Status'],
  'columns':['Doc No','Date','Receipt / Payment','Cheque No','Cheque Date','Company','Bank','Amount VC','Amount LC','Posted','Transferred'],
  'actions':['Upload']
 }
}

def apply_gl_exact_flow(meta):
    source=[]
    for group in ('setup','transactions','controls','reports'):
        source.extend(deepcopy(meta.get(group,[])))
    by_key={x['key']:x for x in source}
    missing=[k for k in GL_SETUP_ORDER+GL_TRANSACTION_ORDER if k not in by_key]
    if missing:
        raise RuntimeError('M3_CLX044_GL_MODULE_MISSING:'+','.join(missing))
    for key,flow in FLOW.items():
        m=by_key[key]
        if 'fields' in flow:m['fields']=flow['fields']
        if 'columns' in flow:m['columns']=flow['columns']
        if 'tabs' in flow:m['tabs']=flow['tabs']
        if 'actions' in flow:m['reference_actions']=flow['actions']
        if 'line_columns' in flow:m['line_columns']=flow['line_columns']
        if 'tree_roots' in flow:m['tree_roots']=flow['tree_roots']
    used=set(GL_SETUP_ORDER+GL_TRANSACTION_ORDER)
    remainder=[x for x in source if x['key'] not in used]
    return {
      'setup':[by_key[k] for k in GL_SETUP_ORDER],
      'transactions':[by_key[k] for k in GL_TRANSACTION_ORDER],
      'controls':[],
      'reports':remainder,
    }

router=APIRouter(prefix='/api/clx044',tags=['CLX-044 GL Exact Flow'])

@router.get('/gl-flow')
def gl_flow():
    return {
      'phase':'CLX-044',
      'menu':['Setup','Transaction','Reports'],
      'setup':GL_SETUP_ORDER,
      'transaction':GL_TRANSACTION_ORDER,
      'screen_count_change':0,
      'module_keys_renamed':False,
      'live_providers':False,
      'real_money':False,
    }

@router.get('/gl-screen/{key}')
def gl_screen(key:str):
    if key not in FLOW: raise HTTPException(404,'No CLX-044 reference flow for GL screen')
    return {'key':key,**FLOW[key]}
