from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY = ROOT / "gateway"
if str(GATEWAY) not in sys.path:
    sys.path.insert(0, str(GATEWAY))

os.environ.setdefault("SEMANTIC_CACHE_TTL", "3600")
os.environ.setdefault("CONTEXT_EDGE_THRESHOLD_TOKENS", "8192")
os.environ.setdefault("CONTEXT_CLOUD_THRESHOLD_TOKENS", "65536")

