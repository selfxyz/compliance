"""Cargo.lock, Solidity SPDX headers, Circom includes and git submodules."""
import os, re, sys, tomllib

from repo import gitgrep, gitshow, pnpm_lock, split, tree


def cargo_rows(a, lk, first_party):
    rows = []
    for path in split(a.cargo_lock):
        txt = gitshow(a, path)
        if txt is None:
            sys.exit(f"Cargo.lock not found at this ref: {path}")
        lock = tomllib.loads(txt)
        man = tomllib.loads(gitshow(a, os.path.join(os.path.dirname(path), "Cargo.toml")) or "")
        root = (man.get("package") or {}).get("name")
        devonly = set(man.get("dev-dependencies", {})) - set(man.get("dependencies", {})) - set(man.get("build-dependencies", {}))
        byname = {}
        for p in lock.get("package", []):
            byname.setdefault(p["name"], []).append(p)

        def resolve(dep):
            parts = dep.split(" ")
            c = byname.get(parts[0], [])
            c = [p for p in c if len(parts) > 1 and p["version"] == parts[1]] or c
            return c[0] if c else None

        rp = next((p for p in lock.get("package", []) if p["name"] == root), None)
        prod = None
        if rp:
            prod, q = set(), [resolve(d) for d in rp.get("dependencies", []) if d.split(" ")[0] not in devonly]
            while q:
                p = q.pop()
                if p and (p["name"], p["version"]) not in prod:
                    prod.add((p["name"], p["version"]))
                    q += [resolve(d) for d in p.get("dependencies", [])]
        pkgs = [p for p in lock.get("package", []) if p["name"] != root]
        ext = [(p["name"], p["version"]) for p in pkgs if p.get("source", "").startswith("registry+")]
        lic = lk.cargo(sorted(ext))
        for p in pkgs:
            k = (p["name"], p["version"])
            if k in lic:
                l, src = lic[k]["lic"].replace(" / ", " OR "), lic[k]["src"]
            else:
                l, src = "", ("first-party, not looked up" if not p.get("source") else "git/url dependency, not looked up")
            rows.append(dict(eco="cargo", name=k[0], version=k[1], licence=l, source=src, direct="",
                             scope="prod" if (prod is None or k in prod) else "dev"))
    return rows


def solidity_rows(a):
    """One row per (folder, SPDX identifier) for the Solidity sources under the given folders."""
    rows = []
    for d in split(a.solidity):
        d = d.rstrip("/")
        sols = [n for _, _, n in tree(a) if n.startswith(d + "/") and n.endswith(".sol")]
        spdx_of = {}
        for path, text in gitgrep(a, "SPDX-License-Identifier:", [d]):
            m = re.search(r"SPDX-License-Identifier:\s*([^\s*]+(?:\s+(?:OR|AND|WITH)\s+[^\s*]+)*)", text)
            if m and path.endswith(".sol"):
                spdx_of.setdefault(path, m.group(1).strip())
        groups = {}
        for f in sols:
            rel = f[len(d) + 1:]
            top = rel.split("/")[0] if "/" in rel else "."
            groups.setdefault((top, spdx_of.get(f, "")), []).append(f)
        for (top, lic), fs in sorted(groups.items()):
            where = d if top == "." else f"{d}/{top}"
            scope = "dev" if re.search(r"(^|/)(tests?|mocks?|example)s?(/|$)", where, re.I) else "prod"
            rows.append(dict(eco="solidity-source", name=f"{where}: {len(fs)} .sol file{'s' if len(fs) != 1 else ''}", version="", licence=lic, scope=scope, direct="",
                             source="SPDX-License-Identifier header in each file" if lic else "no SPDX-License-Identifier header"))
    return rows


def circom_rows(a, npm_rows_):
    """Packages that circom sources include, matched to the npm rows for version and licence."""
    by_name = {}
    for r in npm_rows_:
        by_name.setdefault(r["name"], r)
    users = {}
    for d in split(a.circom):
        for path, text in gitgrep(a, r'^[[:space:]]*include[[:space:]]+"', [d.rstrip("/")]):
            m = re.search(r'include\s+"([^"]+)"', text)
            if not m or not path.endswith(".circom"):
                continue
            inc = m.group(1)
            if "node_modules/" in inc:
                inc = inc.split("node_modules/")[-1]
            elif inc.startswith(".") or "/" not in inc:
                continue  # a local file
            segs = inc.split("/")
            pkg = "/".join(segs[:2]) if segs[0].startswith("@") else segs[0]
            users.setdefault(pkg, set()).add(path)
    alias = {}
    lock = pnpm_lock(a)
    if lock:
        imps = lock.get("importers") or {}
        for root in split(a.importer):
            for sec in ("dependencies", "devDependencies", "optionalDependencies"):
                for n, x in (imps.get(root, {}).get(sec) or {}).items():
                    v = x.get("version", "")
                    if "@" in v[1:] and not v[0].isdigit() and not v.startswith(("http", "link:", "workspace:")):
                        alias[n] = re.sub(r"\(.*$", "", v)[:re.sub(r"\(.*$", "", v).rindex("@")]
    rows = []
    for pkg, fs in sorted(users.items()):
        r = by_name.get(pkg) or by_name.get(alias.get(pkg, ""))
        if pkg in alias and r:
            pkg = f"{pkg} (npm alias of {alias[pkg]})"
        rows.append(dict(eco="circom-include", name=pkg, version=r["version"] if r else "", licence=r["licence"] if r else "", scope="prod", direct="direct",
                         source=(f"included by {len(fs)} circom file{'s' if len(fs) != 1 else ''}; " + (f"licence of the npm package ({r['source']})" if r else "not an npm dependency of this part, not looked up"))))
    return rows


def submodule_rows(a, lk, first_party):
    gm = gitshow(a, ".gitmodules")
    if gm is None:
        return []
    mods, cur = [], None
    for line in gm.splitlines():
        m = re.match(r'\s*\[submodule\s+"([^"]+)"\]', line)
        if m:
            cur = {"path": "", "url": ""}
            mods.append(cur)
        m = re.match(r"\s*(path|url)\s*=\s*(\S+)", line)
        if m and cur is not None:
            cur[m.group(1)] = m.group(2)
    shas = {name: sha for kind, sha, name in tree(a) if kind == "commit"}
    whole = a.path in ("", ".")
    mods = [m for m in mods if whole or m["path"].startswith(a.path.rstrip("/") + "/")]
    slug = lambda u: re.sub(r"\.git$", "", re.sub(r"^(https://|git@)github\.com[/:]", "", u))
    isfp = lambda u: any(x.strip("@/").lower() in u.lower() for x in first_party)
    lic = lk.github(sorted(slug(m["url"]) for m in mods if "github.com" in m["url"] and not isfp(m["url"])))
    rows = []
    for m in mods:
        e = lic.get(slug(m["url"]), {"lic": "", "src": "first-party, not looked up" if isfp(m["url"]) else "not looked up"})
        rows.append(dict(eco="git-submodule", name=f"{m['path']} ({slug(m['url'])})", version=shas.get(m["path"], "")[:12], licence=e["lic"], source=e["src"], scope="prod", direct="direct"))
    return rows
