from __future__ import annotations

import hashlib
import json
from typing import Any


def topology_content_sha256(topology: dict[str, Any]) -> str:
    """Return a deterministic fingerprint of the complete topology payload."""
    canonical = json.dumps(
        topology,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
