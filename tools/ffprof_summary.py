"""Dev-only: inclusive sample counts per frame label in a Gecko profile's content main thread."""
import collections, json, sys

prof = json.load(open(sys.argv[1]))
def threads(p):
    yield from p.get("threads", [])
    for sub in p.get("processes", []):
        yield from threads(sub)
for t in threads(prof):
    if t.get("name") != "GeckoMain" or t.get("processType") not in ("tab", "web", None) or "samples" not in t:
        continue
    strings = t.get("stringTable") or t.get("stringArray")
    st, ft, sm = t["stackTable"], t["frameTable"], t["samples"]
    sidx = st["schema"]; fidx = ft["schema"]; smidx = sm["schema"]
    stacks, frames = st["data"], ft["data"]
    def name(fr):
        v = frames[fr][fidx["location"]]
        return strings[v] if isinstance(v, int) else str(v)
    incl = collections.Counter(); n = 0
    for s in sm["data"]:
        sk = s[smidx["stack"]]
        if sk is None:
            continue
        n += 1; seen = set()
        while sk is not None:
            nm = name(stacks[sk][sidx["frame"]]); 
            if nm not in seen:
                incl[nm] += 1; seen.add(nm)
            sk = stacks[sk][sidx["prefix"]]
    if n < 200:
        continue
    print(f"--- {t.get('processName')} {t.get('processType')} samples {n}")
    for nm, c in incl.most_common(int(sys.argv[2]) if len(sys.argv) > 2 else 45):
        print(f"{100 * c / n:5.1f}% {nm[:150]}")
