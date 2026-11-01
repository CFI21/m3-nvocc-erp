"""UNECE UN/LOCODE multi-part release import: fail closed on missing parts."""
import csv
import pytest
from app import unlocode_release as source


def _part(path, rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",encoding="utf-8",newline="") as stream:
        csv.writer(stream).writerows(rows)


def test_official_three_part_release_is_deduplicated(monkeypatch,tmp_path):
    parts=tuple(tmp_path/f"UNLOCODE CodeListPart{i}.csv" for i in (1,2,3))
    monkeypatch.setattr(source,"PARTS",parts)
    _part(parts[0],[["","NL","RTM","Rotterdam","Rotterdam","ZH","1","AA"]])
    _part(parts[1],[["","DE","HAM","Hamburg","Hamburg","HH","1","AA"]])
    _part(parts[2],[["","NL","RTM","Rotterdam","Rotterdam","ZH","1","AA"]])
    rows=source.catalog()
    assert {r["unlocode"] for r in rows}=={"NLRTM","DEHAM"}
    assert {r["source"] for r in rows}=={"unece_release"}
    assert all(r["country_name"]=="" for r in rows)


def test_partial_official_release_does_not_fall_back_silently(monkeypatch,tmp_path):
    parts=tuple(tmp_path/f"UNLOCODE CodeListPart{i}.csv" for i in (1,2,3))
    monkeypatch.setattr(source,"PARTS",parts)
    _part(parts[0],[["","NL","RTM","Rotterdam","Rotterdam"]])
    with pytest.raises(RuntimeError,match="UNLOCODE_RELEASE_INCOMPLETE"):
        source.catalog()


def test_release_reader_rejects_headers_and_invalid_rows(tmp_path):
    path=tmp_path/"codes.csv"
    _part(path,[
        ["Ch","Country","Location","Name","NameWoDiacritics"],
        ["","NL","RTM","Rotterdam","Rotterdam"],
        ["","XX","LONG","Invalid","Invalid"],
        ["","NL","AMS","","Amsterdam"],
    ])
    rows=list(source._release_rows(path))
    assert [r["unlocode"] for r in rows]==["NLRTM","NLAMS"]


def test_conflicting_duplicate_across_release_parts_is_rejected(monkeypatch,tmp_path):
    parts=tuple(tmp_path/f"UNLOCODE CodeListPart{i}.csv" for i in (1,2,3))
    monkeypatch.setattr(source,"PARTS",parts)
    _part(parts[0],[["","NL","RTM","Rotterdam","Rotterdam"]])
    _part(parts[1],[["","DE","HAM","Hamburg","Hamburg"]])
    _part(parts[2],[["","NL","RTM","Conflicting Port","Conflicting Port"]])
    with pytest.raises(RuntimeError,match="UNLOCODE_RELEASE_CONFLICT:NLRTM"):
        source.catalog()


def test_legacy_catalog_rejects_invalid_and_duplicate_conflicts(monkeypatch,tmp_path):
    parts=tuple(tmp_path/f"UNLOCODE CodeListPart{i}.csv" for i in (1,2,3))
    monkeypatch.setattr(source,"PARTS",parts)
    legacy=tmp_path/"legacy.csv"
    monkeypatch.setattr(source,"LEGACY",legacy)
    _part(legacy,[
        ["unlocode","country_code","country_name","port_code","port_name"],
        ["NLRTM","NL","Netherlands","RTM","Rotterdam"],
        ["NLRTM","NL","Netherlands","RTM","Wrong duplicate"],
    ])
    with pytest.raises(RuntimeError,match="UNLOCODE_CATALOG_CONFLICT:NLRTM"):
        source.catalog()
    _part(legacy,[
        ["unlocode","country_code","country_name","port_code","port_name"],
        ["NLRTM","DE","Germany","RTM","Rotterdam"],
    ])
    with pytest.raises(RuntimeError,match="UNLOCODE_CATALOG_INVALID_RECORD"):
        source.catalog()


def test_legacy_catalog_requires_existing_nonempty_source(monkeypatch,tmp_path):
    monkeypatch.setattr(source,"PARTS",tuple(tmp_path/f"part{i}.csv" for i in range(3)))
    legacy=tmp_path/"legacy.csv"
    monkeypatch.setattr(source,"LEGACY",legacy)
    with pytest.raises(RuntimeError,match="UNLOCODE_CATALOG_SOURCE_MISSING"):
        source.catalog()
    _part(legacy,[["unlocode","country_code","port_code","port_name"]])
    with pytest.raises(RuntimeError,match="UNLOCODE_CATALOG_EMPTY"):
        source.catalog()


def test_deleted_unece_code_is_reference_only(tmp_path):
    path=tmp_path/"part.csv"
    _part(path,[["X","NL","RTM","Rotterdam","Rotterdam"],["","DE","HAM","Hamburg","Hamburg"]])
    rows=list(source._release_rows(path))
    assert rows[0]["change_indicator"]=="X"
    assert rows[0]["eligible_for_activation"] is False
    assert rows[1]["eligible_for_activation"] is True
