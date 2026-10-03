from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.screen_catalog import build_catalog

OUT = Path("artifacts/m3_stage0_baseline_snapshot.json")


def canonical_screen_rows(catalog: dict) -> list[dict]:
    rows = []
    for screen in catalog["screens"]:
        rows.append(
            {
                "screen_id": screen["screen_id"],
                "route": screen["route"],
                "domain": screen["domain"],
                "submenu": screen["submenu"],
                "roles": sorted(screen.get("roles", [])),
            }
        )
    return sorted(rows, key=lambda x: x["screen_id"])


def main() -> None:
    catalog = build_catalog()
    rows = canonical_screen_rows(catalog)
    raw = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload = {
        "project": "M3 NVOCC ERP",
        "artifact_type": "STAGE0_BASELINE_SNAPSHOT",
        "human_review_required": True,
        "production_action": False,
        "real_data_used": False,
        "baseline_screen_count": catalog["baseline_screen_count"],
        "screen_count": catalog["screen_count"],
        "extension_count": catalog["extension_count"],
        "screen_count_policy": catalog["screen_count_policy"],
        "catalog_sha256": hashlib.sha256(raw).hexdigest(),
        "screens": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"WROTE {OUT} screens={payload['screen_count']} sha256={payload['catalog_sha256']}")


if __name__ == "__main__":
    main()
