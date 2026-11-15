#!/usr/bin/env python3
"""Ban property names of the form on<X> when <X> is a member of the same file.

Qt parses `onX` as the signal-handler slot for member `X` (the same
convention as `onXChanged`). When a type declares a property named
`onX` while a sibling `x` exists, the engine resolves the name through
the handler path and the declared binding silently never installs —
the property stays at its type default (#000000 for color) with no
compile error. This is exactly what happened to `onAccent` next to
`accent` (commit fbd15a8d5): every accent-face ink consumer got black.

False-positive guard: only same-file collision is flagged; `on`+Upper
names are also caught. Exempt one line with `// nosd:on-ok <reason>`
where the reason is mandatory.

Usage:
  Scripts/dev/check-on-signal-collision.py    # exit 1 on any hit
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXEMPT_RE = re.compile(r"//\s*nosd:on-ok\s+\S")

DECL = re.compile(
    r"^\s*(?:readonly\s+|default\s+)*property\s+(?:alias\s+|\w[\w.<>\[\]]*\s+)+(\w+)|"
    r"^\s*signal\s+(\w+)"
)


def member_names(lines):
    names = []
    for line in lines:
        m = DECL.match(line)
        if m:
            names.append((m.group(1) or m.group(2), line))
    return names


def main():
    hits = []
    for path in sorted(REPO.rglob("*.qml")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        names = {n for n, _ in member_names(lines)}
        for i, line in enumerate(lines, 1):
            m = DECL.match(line)
            if not m or not m.group(1) or m.group(1)[0:2] != "on" or not m.group(1)[2:3].isupper():
                continue
            on_name = m.group(1)
            sibling = on_name[2].lower() + on_name[3:]
            if sibling in names and not EXEMPT_RE.search(line):
                rel = path.relative_to(REPO)
                hits.append(f"{rel}:{i}: `{on_name}` collides with member `{sibling}` — rename the property")

    for h in hits:
        print(h)
    if hits:
        print(f"\n{len(hits)} on<X>/<X> name collision(s) — the property's binding never installs.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
