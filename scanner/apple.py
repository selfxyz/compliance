"""CocoaPods (Podfile.lock, podspecs) and Swift packages."""
import json, re, sys

from licences import norm
from repo import gitshow, split


def pod_rows(a, npm_lic, lk, first_party):
    txt = gitshow(a, a.podfile_lock) if a.podfile_lock else None
    if txt is None:
        return []
    sec, pods, ext, cur, trunk_names, repo = None, {}, {}, None, set(), None
    for line in txt.splitlines():
        m = re.match(r"^([A-Z][A-Z ]+):\s*$", line)
        if m:
            sec = m.group(1)
            continue
        if sec == "PODS":
            m = re.match(r"^  - \"?([^\s(\"]+) \(([^)]+)\)", line)
            if m:
                pods.setdefault(m.group(1).split("/")[0], m.group(2))
        elif sec == "SPEC REPOS":
            m = re.match(r"^  \"?([^\":]+)\"?:\s*$", line)
            if m:
                repo = m.group(1)
            m = re.match(r"^    - \"?([^\"\n]+)\"?", line)
            if m and repo == "trunk":
                trunk_names.add(m.group(1))
        elif sec == "EXTERNAL SOURCES":
            m = re.match(r"^  \"?([^\":]+)\"?:\s*$", line)
            if m:
                cur = m.group(1).split("/")[0]
                continue
            m = re.match(r"^    :(\w+): \"?([^\"\n]+)\"?", line)
            if m and cur and cur not in ext:
                ext[cur] = (m.group(1), m.group(2))
    isfp = lambda n: any(x.strip("@/").lower() in n.lower() for x in first_party)
    trunk = lk.pods(sorted((n, v) for n, v in pods.items() if n in trunk_names and not isfp(n)))
    rows = []
    for n, v in sorted(pods.items()):
        if isfp(n):
            l, src = "", "first-party, not looked up"
        elif (n, v) in trunk:
            l, src = norm(trunk[(n, v)]["lic"]), trunk[(n, v)]["src"]
        elif n not in ext:
            l, src = "", "not in the trunk spec repo and no external source, not looked up"
        else:
            kind, where = ext[n]
            m = re.search(r"node_modules/((?:@[^/]+/)?[^/]+)", where)
            hit = [x for x in npm_lic if m and x[0] == m.group(1)]
            if kind == "path" and hit:
                l, src = npm_lic[hit[0]], f"npm package {m.group(1)} (local pod)"
            else:
                l, src = "", f"external source ({kind}: {where}), not looked up"
        rows.append(dict(eco="cocoapods", name=n, version=v, licence=l, source=src, scope="prod", direct=""))
    return rows


def podspec_rows(a, lk, first_party):
    rows = []
    for path in split(a.podspec):
        txt = gitshow(a, path)
        if txt is None:
            sys.exit(f"podspec not found at this ref: {path}")
        for m in re.finditer(r"\.dependency\s+['\"]([^'\"/]+)[^'\"]*['\"](?:\s*,\s*['\"]([^'\"]+)['\"])?", txt):
            name, req = m.group(1), m.group(2) or ""
            vm = re.match(r"^(?:~>|>=|=)?\s*([\d.]+)$", req)
            e = lk.pods([(name, vm.group(1))])[(name, vm.group(1))] if vm and not any(x.strip("@/").lower() in name.lower() for x in first_party) else {"lic": "", "src": ""}
            rows.append(dict(eco="cocoapods", name=name, version=req or "any", licence=norm(e["lic"]), scope="prod", direct="direct",
                             source=(e["src"] + " (looked up at the lowest version the requirement allows)") if e["lic"] else "podspec requirement, not looked up"))
    return rows


def swift_rows(a, lk, first_party):
    pk = {}
    for path in split(a.swift):
        txt = gitshow(a, path)
        if txt is None:
            sys.exit(f"swift file not found at this ref: {path}")
        if path.endswith(".resolved"):
            d = json.loads(txt)
            for p in d.get("pins") or d.get("object", {}).get("pins", []):
                url, st = p.get("location") or p.get("repositoryURL", ""), p.get("state", {})
                pk[url] = (st.get("version") or (st.get("revision") or "")[:12], "resolved")
        else:
            for m in re.finditer(r"\.package\(\s*(?:name:\s*\"[^\"]*\"\s*,\s*)?url:\s*\"([^\"]+)\"\s*,\s*(.*)$", txt, re.M):
                req = re.sub(r"\)\s*,?\s*(//.*)?$", "", re.sub(r"\s+", " ", m.group(2)).strip()).replace('"', "'")
                pk.setdefault(m.group(1), (req, "declared"))
    slug = lambda u: re.sub(r"\.git$", "", re.sub(r"^(https://|git@)github\.com[/:]", "", u))
    isfp = lambda n: any(x.strip("@/").lower() in n.lower() for x in first_party)
    lic = lk.github(sorted(slug(u) for u in pk if "github.com" in u and not isfp(u)))
    rows = []
    for url, (ver, how) in sorted(pk.items()):
        n = slug(url)
        if isfp(url):
            l, src = "", "first-party, not looked up"
        else:
            e = lic.get(n, {"lic": "", "src": ""})
            l, src = e["lic"], e["src"] + ("" if how == "resolved" else " (version is the declared requirement, not a resolved pin)")
        rows.append(dict(eco="swiftpm", name=n, version=ver, licence=l, source=src, scope="prod", direct="direct" if how == "declared" else ""))
    return rows
