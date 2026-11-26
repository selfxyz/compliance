"""npm dependencies from yarn.lock and bun.lock."""
import json, re, sys

from repo import gitshow


def yarnlock_rows(a, lk, first_party):
    """Yarn berry lockfile. Argument: "path/to/yarn.lock:workspace1,workspace2" (workspace folders whose
    `dependencies` are the runtime roots). Everything else in the lockfile is dev scope."""
    if not a.yarn_lock:
        return []
    path, _, wss = a.yarn_lock.partition(":")
    txt = gitshow(a, path)
    if txt is None:
        sys.exit(f"yarn.lock not found at this ref: {path}")
    entries, by_desc, cur, block = [], {}, None, None
    for line in txt.splitlines():  # reads both yarn berry and yarn classic (v1) lockfiles
        if line and not line.startswith((" ", "#", "__metadata")) and line.endswith(":"):
            cur = {"descs": [d.strip().strip('"') for d in line[:-1].split(", ")], "deps": {}, "version": ""}
            entries.append(cur)
            for d in cur["descs"]:
                by_desc[d] = cur
            block = None
        elif cur is not None and line.startswith("  ") and not line.startswith("    "):
            t = line.strip()
            m = re.match(r'version:?\s+"?([^"]+)"?$', t)
            if m:
                cur["version"] = m.group(1)
            block = t[:-1] if t.endswith(":") else None
        elif cur is not None and line.startswith("    ") and block in ("dependencies", "optionalDependencies"):
            m = re.match(r'"?([^"\s:]+)"?:?\s+"?([^"]+)"?$', line.strip())
            if m:
                cur["deps"][m.group(1)] = m.group(2)
    def name_of(d):  # "alias@npm:real@range" is listed under the real package name
        alias, rest = d[:d.index("@", 1)], d[d.index("@", 1) + 1:]
        if rest.startswith("npm:") and "@" in rest[5:]:
            return rest[4:4 + rest[4:].index("@", 1)]
        return alias
    roots = []
    for ws in [x.strip() for x in wss.split(",") if x.strip()]:
        pj = json.loads(gitshow(a, "package.json" if ws == "." else f"{ws}/package.json") or "{}")
        wentry = next((e for e in entries if any(d.endswith(f"@workspace:{ws}") for d in e["descs"])), None)
        for dep, rng in (pj.get("dependencies") or {}).items():
            if wentry and dep in wentry["deps"]:
                roots.append(f"{dep}@{wentry['deps'][dep]}")
            elif not wentry:  # yarn classic has no workspace entry: the range comes from package.json
                roots.append(f"{dep}@{rng}")
    prod, q = set(), list(roots)
    while q:
        d = q.pop()
        e = by_desc.get(d) or by_desc.get(d.replace("@npm:", "@", 1))
        if e is None or id(e) in prod:
            continue
        prod.add(id(e))
        deps = e["deps"]
        wsd = next((x for x in e["descs"] if "@workspace:" in x), None)
        if wsd:  # a workspace entry lists dev dependencies too: follow only its package.json dependencies
            wp = wsd.split("@workspace:", 1)[1]
            wpj = json.loads(gitshow(a, "package.json" if wp == "." else f"{wp}/package.json") or "{}")
            keep = set(wpj.get("dependencies") or {}) | set(wpj.get("optionalDependencies") or {})
            deps = {n: r for n, r in deps.items() if n in keep}
        q += [f"{n}@{r}" for n, r in deps.items()]
    found = {}
    for e in entries:
        if any("@workspace:" in d for d in e["descs"]) or not e["version"]:
            continue
        key = (name_of(e["descs"][0]), e["version"])
        if id(e) in prod or key not in found:
            found[key] = "prod" if id(e) in prod else found.get(key, "dev")
    fp = lambda n: any(n.startswith(x) for x in first_party)
    look = lk.npm(sorted(k for k in found if not fp(k[0])))
    return [dict(eco="npm", name=n, version=v, licence="" if fp(n) else look[(n, v)]["lic"], scope=sc, direct="",
                 source="first-party (registry package), not looked up" if fp(n) else look[(n, v)]["src"]) for (n, v), sc in sorted(found.items())]


def bunlock_rows(a, lk, first_party):
    """bun.lock (the text lockfile). Argument: "path/to/bun.lock:workspace1,workspace2" (workspace folders, relative
    to the lockfile, whose dependencies are the runtime roots). Everything else in the lockfile is dev scope."""
    if not a.bun_lock:
        return []
    path, _, wss = a.bun_lock.partition(":")
    txt = gitshow(a, path)
    if txt is None:
        sys.exit(f"bun.lock not found at this ref: {path}")
    d = json.loads(re.sub(r",(\s*[}\]])", r"\1", txt))  # bun.lock is JSON with trailing commas
    pk, wsp = d.get("packages") or {}, d.get("workspaces") or {}
    info = lambda v: next((x for x in v[1:] if isinstance(x, dict)), {})
    runtime = lambda x: {**(x.get("dependencies") or {}), **(x.get("optionalDependencies") or {})}

    def resolve(parent, dep):  # "parent/dep" is a copy nested under parent; plain "dep" is the hoisted one
        while parent:
            if f"{parent}/{dep}" in pk:
                return f"{parent}/{dep}"
            parent = parent.rpartition("/")[0]
        return dep if dep in pk else None

    prod, q = set(), []
    for ws in [x.strip() for x in wss.split(",") if x.strip()]:
        w = wsp.get("" if ws == "." else ws) or {}
        q += [(w.get("name", ""), dep) for dep in runtime(w)]
    while q:
        k = resolve(*q.pop())
        if k is None or k in prod:
            continue
        prod.add(k)
        ident = pk[k][0]
        if "@workspace:" in ident:  # a workspace package: follow its own runtime dependencies
            w = wsp.get(ident.split("@workspace:", 1)[1]) or {}
            q += [(w.get("name", ""), dep) for dep in runtime(w)]
        else:
            q += [(k, dep) for dep in runtime(info(pk[k]))]
    found = {}
    for k, v in pk.items():
        name, _, ver = v[0].rpartition("@")
        if "@workspace:" in v[0] or not name:
            continue
        if ver.startswith("npm:") or "@npm:" in v[0]:  # alias@npm:real@version
            name, _, ver = v[0].split("@npm:", 1)[1].rpartition("@")
        key = (name, ver)
        if k in prod or key not in found:
            found[key] = "prod" if k in prod else found.get(key, "dev")
    fp = lambda n: any(n.startswith(x) for x in first_party)
    isurl = lambda v: not re.match(r"^\d", v)
    look = lk.npm(sorted(k for k in found if not fp(k[0]) and not isurl(k[1])))
    rows = []
    for (n, v), sc in sorted(found.items()):
        if fp(n):
            l, src = "", "first-party (registry package), not looked up"
        elif isurl(v):
            l, src = "", "git/url dependency, not looked up"
        else:
            l, src = look[(n, v)]["lic"], look[(n, v)]["src"]
        rows.append(dict(eco="npm", name=n, version=v, licence=l, source=src, scope=sc, direct=""))
    return rows
