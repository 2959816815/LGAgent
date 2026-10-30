import sys
from compression import zstd

path = sys.argv[1]
tail_chars = int(sys.argv[2]) if len(sys.argv) > 2 else 3000

raw = open(path, "rb").read()
try:
    text = zstd.decompress(raw).decode("utf-8", "replace")
    print("single-call chars:", len(text))
except Exception as exc:  # multi-frame: decompress each frame separately
    print("single-call failed:", exc)
    magic = b"\x28\xb5\x2f\xfd"
    offsets = []
    i = raw.find(magic)
    while i != -1:
        offsets.append(i)
        i = raw.find(magic, i + 4)
    print("frames found:", len(offsets))
    parts = []
    for n, start in enumerate(offsets):
        end = offsets[n + 1] if n + 1 < len(offsets) else len(raw)
        try:
            parts.append(zstd.decompress(raw[start:end]).decode("utf-8", "replace"))
        except Exception as exc2:
            parts.append(f"\n<<frame {n} error: {exc2}>>\n")
    text = "".join(parts)
    print("multi-frame chars:", len(text))

print("--- TAIL ---")
print(text[-tail_chars:])
