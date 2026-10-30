"""XLSX bulk reader: authorization, read-only parsing and row bounds."""
import base64, io
import pytest
from fastapi import HTTPException
from openpyxl import Workbook
from app import masterdata as md

class FakeConnection:
    def close(self): pass

def workbook_bytes(count=1):
    book=Workbook();sheet=book.active
    sheet.append(["record_key","operation","name","reason"])
    for i in range(count):
        sheet.append([f"AG-{i}","CREATE",f"Agent {i}","Staged import"])
    out=io.BytesIO();book.save(out);book.close()
    return base64.b64encode(out.getvalue()).decode()

def test_excel_parse_is_read_only_and_bounded(monkeypatch):
    monkeypatch.setattr(md,"connect",lambda:FakeConnection())
    monkeypatch.setattr(md,"authenticated_actor",lambda c,t:({"user_ref":"maker"}, {"MASTER_DATA"}))
    monkeypatch.setattr(md,"can_make_roles",lambda roles:True)
    result=md.bulk_parse_xlsx(md.BulkXlsxInput(content_base64=workbook_bytes(2)),x_m3_session="synthetic-token")
    assert result["columns"]==["record_key","operation","name","reason"]
    assert result["total"]==2
    assert result["rows"][0][0]=="AG-0"
    with pytest.raises(HTTPException) as e:
        md.bulk_parse_xlsx(md.BulkXlsxInput(content_base64=workbook_bytes(101)),x_m3_session="synthetic-token")
    assert e.value.status_code==422

def test_excel_parse_denies_unauthorized_user(monkeypatch):
    monkeypatch.setattr(md,"connect",lambda:FakeConnection())
    monkeypatch.setattr(md,"authenticated_actor",lambda c,t:({"user_ref":"viewer"}, {"VIEWER"}))
    monkeypatch.setattr(md,"can_make_roles",lambda roles:False)
    with pytest.raises(HTTPException) as e:
        md.bulk_parse_xlsx(md.BulkXlsxInput(content_base64=workbook_bytes()),x_m3_session="synthetic-token")
    assert e.value.status_code==403

def test_excel_parse_rejects_non_workbook(monkeypatch):
    monkeypatch.setattr(md,"connect",lambda:FakeConnection())
    monkeypatch.setattr(md,"authenticated_actor",lambda c,t:({"user_ref":"maker"},{"MASTER_DATA"}))
    monkeypatch.setattr(md,"can_make_roles",lambda roles:True)
    bad=base64.b64encode(b"not a spreadsheet").decode()
    with pytest.raises(HTTPException) as e:
        md.bulk_parse_xlsx(md.BulkXlsxInput(content_base64=bad),x_m3_session="synthetic-token")
    assert e.value.status_code==422
