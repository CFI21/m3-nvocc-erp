from pathlib import Path
import base64, hashlib

EXPECTED = "d0b333e3529cd4adb314e087e4c87ec94c364f2cd7b53d32c6df6606ce92a316"
root = Path(__file__).resolve().parent.parent
data = "".join((root / "bundle" / f"clx016_api_{i:02d}.b64").read_text().strip() for i in range(4))
raw = base64.b64decode(data)
actual = hashlib.sha256(raw).hexdigest()
if actual != EXPECTED:
    raise SystemExit("CLX-016 bundle integrity check failed")
out = root / "m3_clx016_runtime.tar.xz"
out.write_bytes(raw)
print("CLX-016 bundle verified:", actual)
