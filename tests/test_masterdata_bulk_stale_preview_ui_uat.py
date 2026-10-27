"""Ensure the bulk UI never stages a previously previewed file/domain after inputs change."""
from pathlib import Path


def test_bulk_import_invalidates_preview_when_source_changes():
    page=Path("web/index.html").read_text(encoding="utf-8")
    shell=page.split('id="m3-bulk-shell"',1)[1]
    assert "function invalidatePreview()" in shell
    handler=shell.split("function invalidatePreview(){",1)[1].split("\n}",1)[0]
    for clause in (
        "request=null",
        "report=null",
        "byId('m3-bulk-submit').disabled=true",
        "byId('m3-bulk-report').disabled=true",
    ):
        assert clause in handler
    assert "byId('m3-bulk-file').addEventListener('change',invalidatePreview)" in shell
    assert "byId('m3-bulk-domain').addEventListener('change',invalidatePreview)" in shell
    assert "if(!request)return" in shell


def test_bulk_import_disregards_outdated_async_preview_response():
    page=Path('web/index.html').read_text(encoding='utf-8')
    shell=page.split('id="m3-bulk-shell"',1)[1]
    assert 'previewGeneration=0' in shell
    assert 'previewGeneration++;' in shell.split('function invalidatePreview(){',1)[1].split('\n}',1)[0]
    preview= shell.split("byId('m3-bulk-preview').onclick=async()=>{",1)[1].split("byId('m3-bulk-submit').onclick",1)[0]
    assert 'const generation=++previewGeneration;' in preview
    assert 'if(generation!==previewGeneration)return;' in preview
    assert preview.index('if(generation!==previewGeneration)return;') < preview.index('request=result.rejected===0?candidate:null;')
    assert "if(generation===previewGeneration)status('Preview failed: '+e.message)" in preview
