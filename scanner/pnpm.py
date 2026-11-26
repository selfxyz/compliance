"""npm dependencies from pnpm-lock.yaml and package-lock.json."""
import json, os, re, subprocess, sys

from repo import gitshow, pnpm_lock, split, tree


def ws_licence(a, path):
    """Licence field and LICENSE file of a workspace package, read at the scanned commit."""
    try:
        pj = json.loads(subprocess.run(["git", "-C", a.repo, "show", f"{a.sha}:{path}/package.json"], capture_output=True, text=True, check=True).stdout)
    except Exception:
        pj = {}
    files = [n for _, _, n in tree(a) if re.match(rf"{re.escape(path)}/(LICEN[SC]E|COPYING)[^/]*$", n, re.I)]
    return pj.get("license") or "", f"first-party workspace `{path}`: licence field {pj.get('license') or 'MISSING'}; licence file {'present' if files else 'MISSING'}"


def npm_rows(a, lk, first_party):
    """pnpm-lock.yaml at the repo root: the prod and dev closure of the importers in a.importer."""
    L = pnpm_lock(a) if a.importer else None
    if L is None:
        return [], {}
    # A single-package lock has no importers section; lockfile v6 keeps dependency edges under packages["/name@version"].
    imps = L.get("importers") or {".": {s: L.get(s) for s in ("dependencies", "devDependencies", "optionalDependencies")}}
    snaps = L.get("snapshots") or {k.lstrip("/"): v for k, v in (L.get("packages") or {}).items()}
    roots = split(a.importer)
    for r in roots:
        if r not in imps:
            sys.exit(f"importer {r!r} not in pnpm-lock.yaml")

    def key(n, v):
        return v if ("@" in v and not v[0].isdigit() and not v.startswith("http")) else f"{n}@{v}"

    def base(k):
        k = re.sub(r"\(.*$", "", k)
        i = k.rfind("@")
        return k[:i], k[i + 1:]

    ws, ws_scope = {}, {}

    def walk(sections):
        reach, direct, seen_imp, q = {}, set(), set(roots), []
        for root in roots:
            for s in sections:
                for n, x in (imps[root].get(s) or {}).items():
                    q.append((n, x["version"], root, True))
        while q:
            n, ver, imp, top = q.pop(0)
            if ver.startswith(("link:", "workspace:")):
                t = os.path.normpath(os.path.join(imp, ver.split(":", 1)[1]))
                ws[n] = t
                ws_scope.setdefault(n, "prod" if sections[0] == "dependencies" else "dev")
                if t in imps and t not in seen_imp:
                    seen_imp.add(t)
                    for s in ("dependencies", "optionalDependencies"):
                        for n2, x in (imps[t].get(s) or {}).items():
                            q.append((n2, x["version"], t, True))
                continue
            k = key(n, ver)
            if top:
                direct.add(base(k))
            if k in reach:
                continue
            reach[k] = 1
            s = snaps.get(k, {})
            for n2, v2 in {**(s.get("dependencies") or {}), **(s.get("optionalDependencies") or {})}.items():
                q.append((n2, v2, imp, False))
        return {base(k) for k in reach}, direct

    prod, pdirect = walk(["dependencies", "optionalDependencies"])
    dev, ddirect = walk(["devDependencies"])
    dev -= prod
    fp = lambda n: any(n.startswith(x) for x in first_party) or n in ws
    isurl = lambda v: v.startswith("http") or "github" in v or v.startswith("git")
    lic = lk.npm(sorted((n, v) for n, v in prod | dev if not fp(n) and not isurl(v)))
    rows = [dict(eco="npm", name=n, version="workspace", licence=ws_licence(a, t)[0], source=ws_licence(a, t)[1], scope=ws_scope.get(n, "prod"), direct="direct") for n, t in sorted(ws.items())]
    for n, v in sorted(prod | dev):
        scope = "prod" if (n, v) in prod else "dev"
        d = "direct" if (n, v) in (pdirect | ddirect) else "transitive"
        if n in ws:
            l, src = ws_licence(a, ws[n])
        elif fp(n):
            l, src = "", "first-party (registry package), not looked up"
        elif isurl(v):
            l, src = "", "git/url dependency, not looked up"
        else:
            l, src = lic[(n, v)]["lic"], lic[(n, v)]["src"]
        rows.append(dict(eco="npm", name=n, version=v, licence=l, source=src, scope=scope, direct=d))
    return rows, ws


def npmlock_rows(a, lk, first_party):
    found = {}
    for path in split(a.npm_lock):
        txt = gitshow(a, path)
        if txt is None:
            sys.exit(f"package-lock.json not found at this ref: {path}")
        for k, p in json.loads(txt).get("packages", {}).items():
            if "node_modules/" not in k or p.get("link"):
                continue
            key = (k.split("node_modules/")[-1], p.get("version", ""))
            scope = "dev" if p.get("dev") else "prod"
            lic = p.get("license") if isinstance(p.get("license"), str) else ""
            if key not in found or (scope == "prod" and found[key][0] == "dev"):
                found[key] = (scope, lic or found.get(key, ("", ""))[1])
    fp = lambda n: any(n.startswith(x) for x in first_party)
    look = lk.npm(sorted(k for k, v in found.items() if not v[1] and not fp(k[0])))
    rows = []
    for (n, v), (scope, lic) in sorted(found.items()):
        if fp(n):
            l, src = lic, "first-party (registry package), not looked up"
        elif lic:
            l, src = lic, "licence field in package-lock.json"
        else:
            l, src = look[(n, v)]["lic"], look[(n, v)]["src"]
        rows.append(dict(eco="npm", name=n, version=v, licence=l, source=src, scope=scope, direct=""))
    return rows
