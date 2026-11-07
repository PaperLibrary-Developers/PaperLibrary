"""启动器用：检查本机文献库是否已就绪。退出码 0=已就绪，1=未就绪。"""
from __future__ import annotations

import sys
import urllib.request

url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:4321/api/health"
try:
    with urllib.request.urlopen(url, timeout=1.2) as resp:
        raise SystemExit(0 if resp.status == 200 else 1)
except SystemExit:
    raise
except Exception:
    raise SystemExit(1)
