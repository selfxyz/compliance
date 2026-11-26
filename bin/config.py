"""Load a repo's .github/compliance.json (or detect its workspaces) and check it against the repo's files."""
import fnmatch, json, os, sys

import jsonschema

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
CONFIG = ".github/compliance.json"


def fail(msg):
    print(f"::error title=compliance.json::{msg}")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(f"## Compliance plan failed\n\n{msg}\n")
    sys.exit(2)


def within(path, ws):
    return ws == "." or path == ws or path.startswith(ws.rstrip("/") + "/")


def owner_of(path, workspaces):
    """The most specific workspace containing path, or None."""
    best = None
    for w in workspaces:
        if within(path, w["path"]) and (best is None or len(w["path"]) > len(best["path"])):
            best = w
    return best


def publishable(repo, pj_path):
    """A package.json that is set up for publishing. `main` alone does not count: npm init adds it to everything."""
    try:
        with open(os.path.join(repo, pj_path)) as f:
            pj = json.load(f)
    except (OSError, ValueError):
        return False
    return isinstance(pj, dict) and not pj.get("private") and bool(pj.get("name")) and any(k in pj for k in ("publishConfig", "files", "exports"))


def matches(repo, files, marker):
    """Files in `files` that are evidence for `marker` (a glob or a named check)."""
    if marker == "publishable-package-json":
        return [f for f in files if os.path.basename(f) == "package.json" and publishable(repo, f)]
    return [f for f in files if fnmatch.fnmatch(f, "*/" + marker) or fnmatch.fnmatch(f, marker)]


def load(repo, kinds_cfg, files):
    path = os.path.join(repo, CONFIG)
    if not os.path.exists(path):
        return detect(repo, kinds_cfg, files), True
    try:
        with open(path) as f:
            cfg = json.load(f)
    except ValueError as e:
        fail(f"{CONFIG} is not valid JSON: {e}")
    with open(os.path.join(ROOT, "schema", "compliance.schema.json")) as f:
        schema = json.load(f)
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(cfg), key=lambda e: list(e.path))
    if errors:
        fail("; ".join(f"{'/'.join(map(str, e.path)) or '(top)'}: {e.message}" for e in errors[:5]))
    if "kind" in cfg:
        cfg["workspaces"] = [{"path": ".", "kind": cfg.pop("kind")}]
    seen = set()
    for w in cfg["workspaces"]:
        w["path"] = os.path.normpath(w["path"])
        if w["path"] in seen:
            fail(f"workspace path {w['path']!r} is listed twice")
        seen.add(w["path"])
        if w["path"] != "." and not any(within(f, w["path"]) for f in files):
            fail(f"workspace path {w['path']!r} does not exist at this commit")
    return cfg, False


def detect(repo, kinds_cfg, files):
    found = {}
    for rule in kinds_cfg["detect"]:
        for f in matches(repo, files, rule["marker"]):
            kinds = found.setdefault(os.path.dirname(f) or ".", [])
            # A folder gets every kind its files show (the strictest rules win); a fallback rule only fills an empty folder.
            if rule["kind"] not in kinds and not (rule.get("fallback") and kinds):
                kinds.append(rule["kind"])
    # Evidence of a kind (an Xcode project, an AndroidManifest.xml) that no marker caught: the folder that
    # holds it gets that kind too, or becomes a workspace. Detection then always passes its own cross-check.
    for kind, spec in kinds_cfg["kinds"].items():
        for marker in spec["evidence"]:
            for f in matches(repo, files, marker):
                d = os.path.dirname(f) or "."
                d = os.path.dirname(d) or "." if d.endswith((".xcodeproj", ".xcframework")) else d
                owner = max((w for w in found if within(d, w)), key=len, default=d)
                if kind not in found.setdefault(owner, []):
                    found[owner].append(kind)
    found = found or {".": ["tooling"]}  # nothing recognisable: still run the file checks and the secret scan
    print(f"no {CONFIG}: detected {len(found)} workspace(s)")
    return {"version": 1, "mode": "report",
            "workspaces": [{"path": p, "kind": k} for p, k in sorted(found.items())]}


def resolve_profile(ws, kinds_cfg):
    order = kinds_cfg["strictness"]
    ws["kinds"] = ws["kind"] if isinstance(ws["kind"], list) else [ws["kind"]]
    strictest = min((kinds_cfg["kinds"][k]["profile"] for k in ws["kinds"]), key=order.index)
    if ws.get("override"):
        print(f"::warning title=Profile override::{ws['path']}: {strictest} -> {ws['override']['profile']} ({ws['override']['reason']})")
        return ws["override"]["profile"]
    return strictest


def cross_check(repo, ws, workspaces, kinds_cfg, files):
    """Error if the folder holds evidence of a stricter kind than it was declared as.
    Skipped for an override (it carries a reason) and for kinds that are excluded by definition
    (tooling, example, test-app); both are listed in the job summary."""
    if ws.get("override") or ws["profile"] == "excluded":
        return
    order = kinds_cfg["strictness"]
    own = [f for f in files if owner_of(f, workspaces) is ws]
    for kind, spec in kinds_cfg["kinds"].items():
        if order.index(spec["profile"]) >= order.index(ws["profile"]):
            continue
        for marker in spec["evidence"]:
            hits = matches(repo, own, marker)
            if hits:
                fail(f"workspace {ws['path']!r} is declared {'/'.join(ws['kinds'])} ({ws['profile']} rules) "
                     f"but {hits[0]} shows it is {kind} ({spec['profile']} rules). Change its kind, split the "
                     f"folder into its own workspace, or add an override with a reason.")


def uncovered(repo, workspaces, kinds_cfg, files, importers):
    """Dependency manifests that no workspace covers. Plain package.json files are not counted:
    a monorepo root has one, and its packages show up as lockfile importers instead."""
    markers = {r["marker"] for r in kinds_cfg["detect"]} - {"package.json"} | {"package-lock.json", "Cargo.lock"}
    markers |= {m for spec in kinds_cfg["kinds"].values() for m in spec["evidence"]}
    loose = {f for m in markers for f in matches(repo, files, m) if owner_of(f, workspaces) is None}
    loose |= {i + "/ (pnpm importer)" for i in importers if i != "." and owner_of(i, workspaces) is None}
    return sorted(loose)
