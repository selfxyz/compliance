"""Gradle dependencies declared in build.gradle / build.gradle.kts files."""
import re, sys, tomllib

from licences import norm
from repo import gitshow, split


GRADLE_CONF = re.compile(r"(implementation|api|compileonly|runtimeonly|kapt|ksp|annotationprocessor)$", re.I)


def gradle_declared(a):
    """Dependencies declared in the given Gradle files (Groovy or Kotlin DSL), with a version catalog if given.
    Returns ({module: (version, scope)}, {project path: scope})."""
    libs, bundles = {}, {}
    if a.version_catalog:
        cat = tomllib.loads(gitshow(a, a.version_catalog) or "")
        vers = cat.get("versions", {})
        for alias, d in cat.get("libraries", {}).items():
            if isinstance(d, str):
                g, n, v = (d.split(":") + [""])[:3]
            else:
                g, n = (d["module"].split(":") if "module" in d else (d.get("group", ""), d.get("name", "")))
                v = d.get("version", "")
                v = vers.get(v.get("ref", ""), v.get("require", "")) if isinstance(v, dict) else v
            libs[re.sub(r"[-_]", ".", alias)] = (f"{g}:{n}", str(v))
        bundles = {re.sub(r"[-_]", ".", k): [re.sub(r"[-_]", ".", x) for x in v] for k, v in cat.get("bundles", {}).items()}
    mods, projects = {}, {}

    def add(mod, ver, conf):
        scope = "dev" if re.search(r"test|debug|kapt|ksp|annotationprocessor", conf, re.I) else "prod"
        if mod not in mods or (scope == "prod" and mods[mod][1] == "dev"):
            mods[mod] = (ver or "unresolved", scope)

    for path in split(a.gradle_files):
        txt = gitshow(a, path)
        if txt is None:
            sys.exit(f"gradle file not found at this ref: {path}")
        for line in txt.splitlines():
            m = re.match(r"\s*(\w+)\s*[( ]\s*(.*)$", line)
            if not m or not GRADLE_CONF.search(m.group(1)):
                continue
            conf, rest = m.group(1), m.group(2)
            pm = re.search(r"project\(\s*(?:path\s*[:=]\s*)?['\"]([^'\"]+)['\"]", rest)
            cm = re.search(r"['\"]([\w.\-]+):([\w.\-]+)(?::([^'\"@]+))?(?:@\w+)?['\"]", rest)
            lm = re.search(r"libs\.([\w.]+)", rest)
            if pm:
                projects[pm.group(1)] = "prod"
            elif cm:
                add(f"{cm.group(1)}:{cm.group(2)}", cm.group(3) or "", conf)
            elif lm:
                alias = lm.group(1)
                for al in (bundles.get(alias[len("bundles."):], []) if alias.startswith("bundles.") else [alias]):
                    if al in libs:
                        add(libs[al][0], libs[al][1], conf)
                    else:
                        add(f"catalog:{al}", "", conf)
    return mods, projects


def gradle_rows(a, lk, npm_lic, first_party):
    """Maven modules and local Gradle projects declared in a.gradle_files. No transitive dependencies."""
    if not split(a.gradle_files):
        return []
    mods, projects = gradle_declared(a)
    isfp = lambda n: any(n.startswith(x) for x in first_party)
    lic = lk.maven(sorted((n, v) for n, (v, _) in mods.items() if not isfp(n) and not n.startswith("catalog:") and v != "unresolved" and "$" not in v))
    rows = []
    for n, (v, scope) in sorted(mods.items()):
        e = lic.get((n, v), {"lic": "", "src": "first-party, not looked up" if isfp(n) else "version not resolved, not looked up"})
        rows.append(dict(eco="maven", name=n, version=v, licence=norm(e["lic"]), scope=scope, direct="direct", source=e["src"]))
    by_name = {}
    for (n, v), l in npm_lic.items():
        by_name.setdefault(n, l)
    for p in sorted(projects):
        nm = p.lstrip(":")
        hit = next((c for c in (nm, "@" + nm.replace("_", "/", 1)) if c in by_name), None)
        rows.append(dict(eco="gradle-project", name=p, version="", licence=by_name[hit] if hit else "",
                         source=f"npm package {hit} (autolinked module)" if hit else "local Gradle module, not looked up", scope="prod", direct=""))
    return rows
