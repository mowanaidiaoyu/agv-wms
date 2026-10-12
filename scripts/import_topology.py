"""把导出的拓扑 JSON（如 docs/topologies/666.json）导入到本地后端数据库。

用法（先启动后端）：
    python scripts/import_topology.py docs/topologies/666.json

默认请求 http://127.0.0.1:8000/api/v1/topologies（与前端 API_BASE 一致），
也可用第二个参数覆盖，例如：
    python scripts/import_topology.py docs/topologies/666.json http://127.0.0.1:8000/api/v1/topologies
"""

from __future__ import annotations

import json
import sys
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8000/api/v1/topologies"


def main() -> int:
    if len(sys.argv) < 2:
        print(f"用法: python {sys.argv[0]} <拓扑.json> [url]")
        return 1

    path = sys.argv[1]
    url = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_URL

    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"导入成功: id={data.get('id')} name={data.get('name')}")
            return 0
    except urllib.error.HTTPError as e:
        print(f"导入失败 HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}")
        return 1
    except urllib.error.URLError as e:
        print(f"连不上后端 {url}: {e.reason}（先启动后端再试）")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
