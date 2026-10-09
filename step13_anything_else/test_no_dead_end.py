import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import test_no_repeat as T
sa = T.sa
cases = {
 "category":   ("show me kurthis", dict(category_hint="kurthi", search_query="kurthi")),
 "colour":     ("show me red kurthi", dict(category_hint="kurthi", color_hint="red", search_query="red kurthi")),
 "new":        ("new arrivals", dict(intent="NEW_ARRIVALS")),
 "more":       ("anything else", dict(search_query=None, wants_more=True)),
 "more_other": ("show more", dict(intent="OTHER", search_query=None, wants_more=True)),
}
bad = []
for name, (msg, kw) in cases.items():
    T.script(msg, **kw)
    c = "dead_" + name
    if name.startswith("more"):
        T.script("show me kurthis", category_hint="kurthi", search_query="kurthi")
        T.ask("show me kurthis", c)
    for i in range(80):
        out, skus = T.ask(msg, c)
        rep = (out or {}).get("reply") or ""
        if "already seen" in rep.lower() or not skus or len(skus) > 5:
            bad.append("%s call %d: cards=%d reply=%r" % (name, i + 1, len(skus), rep[:70]))
            break
    else:
        print("OK   ", name, "- 80 calls, always 1-5 cards, no dead-end message")
print("DEAD ENDS FOUND:" if bad else "NO DEAD ENDS")
for b in bad: print(" -", b)
