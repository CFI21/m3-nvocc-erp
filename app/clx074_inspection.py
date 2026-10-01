from __future__ import annotations
from fastapi import APIRouter,Header,HTTPException
from pydantic import BaseModel,Field
from typing import Optional
import uuid
from .db import connect,tx
from .clx070_container_master_control import actor,require_action,get_container,audit,now

router=APIRouter(prefix="/api/clx074/mr",tags=["CLX-074 Depot M&R"])

class InspectionBody(BaseModel):
    depot_code:Optional[str]=None
    contractor_code:Optional[str]=None
    condition_before:Optional[str]=None
    csc_result:Optional[str]=None
    safety_result:Optional[str]=None
    cleaning_required:bool=False
    repair_required:bool=False

class DamageBody(BaseModel):
    component_code:str
    location_code:Optional[str]=None
    severity:str="MINOR"
    damage_code:Optional[str]=None
    description:Optional[str]=None
    photo_document_ref:Optional[str]=None
    party_type:Optional[str]=None
    party_code:Optional[str]=None
    cleaning_required:bool=False
    repair_required:bool=True

class DocumentBody(BaseModel):
    document_type:str="DAMAGE_PHOTO"
    file_name:str
    storage_ref:str

class EstimateBody(BaseModel):
    contractor_code:Optional[str]=None
    currency:str="USD"
    labour_cost:float=Field(default=0,ge=0)
    material_cost:float=Field(default=0,ge=0)
    third_party_cost:float=Field(default=0,ge=0)
    cleaning_cost:float=Field(default=0,ge=0)

def ref(p):return p+"-"+uuid.uuid4().hex[:12].upper()

def scoped_inspection(c,a,inspection_ref):
    r=c.execute("SELECT i.*,x.container_no FROM container_inspections i JOIN containers x ON x.id=i.container_id WHERE i.inspection_ref=?",(inspection_ref,)).fetchone()
    if not r:raise HTTPException(404,"Unknown inspection")
    get_container(c,r["container_no"],a)
    return r

@router.post("/containers/{container_no}/inspection")
def create_inspection(container_no:str,b:InspectionBody,x_role:str=Header("DEPOT"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write");tx(c);co=get_container(c,container_no,a)
        if a["role"]=="DEPOT" and b.depot_code and b.depot_code!=a["depot"]:raise HTTPException(403,{"code":"DEPOT_SCOPE_MISMATCH"})
        ir=ref("INS")
        c.execute("""INSERT INTO container_inspections(inspection_ref,container_id,inspection_type,depot_code,contractor_code,status,condition_before,csc_result,safety_result,cleaning_required,repair_required,reinspect_required,inspected_at,maker_role,created_at,updated_at)
                     VALUES(?,?,'EMPTY_RETURN',?,?,'DRAFT',?,?,?,?,?,?,?, ?,?,?)""",
                  (ir,co["id"],b.depot_code or co["depot_code"],b.contractor_code,b.condition_before or co["condition"],b.csc_result,b.safety_result,b.cleaning_required,b.repair_required,b.repair_required,now(),a["role"],now(),now()))
        c.execute("UPDATE containers SET equipment_status='INSPECTION',journey_state='INSPECTION',inspection_hold=true,updated_at=? WHERE id=?",(now(),co["id"]))
        audit(c,a,"MR_INSPECTION_CREATE",co["id"],co["job_id"],dict(co),{"inspection_ref":ir},{});c.execute("COMMIT")
        return {"ok":True,"inspection_ref":ir}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.post("/inspections/{inspection_ref}/damage")
def add_damage(inspection_ref:str,b:DamageBody,x_role:str=Header("DEPOT"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write");tx(c);i=scoped_inspection(c,a,inspection_ref);dr=ref("DMG")
        c.execute("""INSERT INTO container_damage_items(damage_ref,inspection_id,component_code,location_code,severity,damage_code,description,photo_document_ref,responsibility_party_type,responsibility_party_code,cleaning_required,repair_required,created_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",(dr,i["id"],b.component_code,b.location_code,b.severity.upper(),b.damage_code,b.description,b.photo_document_ref,b.party_type,b.party_code,b.cleaning_required,b.repair_required,now()))
        if b.repair_required or b.cleaning_required:
            c.execute("UPDATE container_inspections SET repair_required=?,cleaning_required=?,reinspect_required=true,updated_at=? WHERE id=?",(b.repair_required,b.cleaning_required,now(),i["id"]))
            c.execute("UPDATE containers SET damage_hold=true,inspection_hold=true,equipment_status='HOLD',journey_state='HOLD',updated_at=? WHERE id=?",(now(),i["container_id"]))
        c.execute("COMMIT");return {"ok":True,"damage_ref":dr}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.post("/inspections/{inspection_ref}/documents")
def add_document(inspection_ref:str,b:DocumentBody,x_role:str=Header("DEPOT"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write");tx(c);i=scoped_inspection(c,a,inspection_ref);d=ref("CDOC")
        c.execute("INSERT INTO container_documents(document_ref,container_id,document_type,file_name,storage_ref,status,uploaded_by,uploaded_at) VALUES(?,?,?,?,?,'ACTIVE',?,?)",(d,i["container_id"],b.document_type,b.file_name,b.storage_ref,a["user"],now()))
        c.execute("COMMIT");return {"ok":True,"document_ref":d}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()

@router.post("/inspections/{inspection_ref}/estimate")
def estimate(inspection_ref:str,b:EstimateBody,x_role:str=Header("DEPOT"),x_m3_session:Optional[str]=Header(None,alias="X-M3-Session"),x_agent_scope:Optional[str]=Header(None,alias="X-Agent-Scope"),x_branch_scope:Optional[str]=Header(None,alias="X-Branch-Scope"),x_depot_scope:Optional[str]=Header(None,alias="X-Depot-Scope")):
    c=connect()
    try:
        a=actor(c,x_m3_session,x_role,x_agent_scope,x_branch_scope,x_depot_scope);require_action(a,"write");tx(c);i=scoped_inspection(c,a,inspection_ref)
        total=round(b.labour_cost+b.material_cost+b.third_party_cost+b.cleaning_cost,2);er=ref("EST")
        c.execute("""INSERT INTO container_repair_estimates(estimate_ref,inspection_id,depot_code,contractor_code,currency,labour_cost,material_cost,third_party_cost,cleaning_cost,total_estimate,status,maker_role,created_at,updated_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,'SUBMITTED',?,?,?)""",(er,i["id"],i["depot_code"],b.contractor_code or i["contractor_code"],b.currency,b.labour_cost,b.material_cost,b.third_party_cost,b.cleaning_cost,total,a["role"],now(),now()))
        c.execute("COMMIT");return {"ok":True,"estimate_ref":er,"total_estimate":total,"status":"SUBMITTED"}
    except Exception:
        try:c.execute("ROLLBACK")
        except Exception:pass
        raise
    finally:c.close()
