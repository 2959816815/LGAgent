import json
import sys
from compression import zstd

path = sys.argv[1]
want = int(sys.argv[2]) if len(sys.argv) > 2 else 40

raw = open(path, "rb").read()
text = zstd.decompress(raw).decode("utf-8", "replace")
lines = [ln for ln in text.split("\n") if ln.strip()]
print("total events:", len(lines))
for ln in lines[-want:]:
    try:
        obj = json.loads(ln)
    except Exception:
        print("RAW:", ln[:200])
        continue
    kind = obj.get("type")
    seq = obj.get("seq")
    data = obj.get("data") or {}
    detail = ""
    if kind == "tool/call":
        detail = str(data.get("name")) + " " + str(data.get("arguments"))[:140]
    elif kind == "tool/result":
        detail = str(data.get("callId")) + " ok=" + str(data.get("ok"))
    elif kind in ("turn/end", "step/end", "assistant/attempt"):
        detail = json.dumps(data, ensure_ascii=False)[:180]
    elif kind == "job/registered":
        detail = str(data.get("jobId")) + " " + str(data.get("kind"))
    else:
        detail = json.dumps(data, ensure_ascii=False)[:140]
    print(f"{seq} {kind}: {detail}")
