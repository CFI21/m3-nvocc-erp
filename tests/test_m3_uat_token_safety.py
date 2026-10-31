import importlib.util
from pathlib import Path

path = Path(__file__).resolve().parents[1] / 'scripts' / 'm3_isolated_atomic_bulk_uat.py'
spec = importlib.util.spec_from_file_location('m3_uat', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

def test_token_copy_paste_whitespace():
    assert module.normalize_uat_token('  example-token  ') == 'example-token'

def test_token_preserves_plus_and_equal():
    assert module.normalize_uat_token('abc+def=') == 'abc+def='
