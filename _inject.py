# -*- coding: utf-8 -*-
"""dashboard.html / graph.html 에 최신 스냅샷(JSON) 재주입.
※ re.sub 치환문자열의 \\ 이스케이프 훼손을 피하려고 lambda 치환 사용."""
import re, json, aggregate

data = aggregate.build(write=True)
snap = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
# </script> 조기종결 방지(JSON 문자열 안에 있어도 안전하게)
snap = snap.replace("</", "<\\/")
block = '<script id="snap" type="application/json">' + snap + "</script>"

for fn in ("dashboard.html", "graph.html"):
    h = open(fn, encoding="utf-8").read()
    h2 = re.sub(
        r'<script id="snap" type="application/json">.*?</script>',
        lambda m: block,           # ← lambda라 치환문자열 이스케이프 없음
        h, count=1, flags=re.S)
    open(fn, "w", encoding="utf-8").write(h2)
    # 검증: 다시 읽어 파싱
    m = re.search(r'<script id="snap" type="application/json">(.*?)</script>', h2, re.S)
    d = json.loads(m.group(1).replace("<\\/", "</"))
    print(fn, "OK — agents", len(d["agents"]), "| bytes", len(snap))

import shutil
shutil.copy("dashboard.html", "index.html")
print("index.html synced")
