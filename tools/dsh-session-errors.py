import json
import re
import sys
from compression import zstd

path = sys.argv[1]
raw = open(path, "rb").read()
text = zstd.decompress(raw).decode("utf-8", "replace")
lines = [ln for ln in text.split("\n") if ln.strip()]

msgs = {}
for ln in lines[-400:]:
    if '"failure"' not in ln and '"error"' not in ln:
        continue
    try:
        obj = json.loads(ln)
    except Exception:
        continue
    blob = json.dumps(obj, ensure_ascii=False)
    for m in re.finditer(r'"message"\s*:\s*"((?:[^"\\]|\\.){0,600})"', blob):
        key = m.group(1)[:400]
        msgs[key] = msgs.get(key, 0) + 1

print("distinct error messages in last 400 events:", len(msgs))
for k, v in sorted(msgs.items(), key=lambda x: -x[1])[:8]:
    print("---")
    print("count:", v)
    print(k)
