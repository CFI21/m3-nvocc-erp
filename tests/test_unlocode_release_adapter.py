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
