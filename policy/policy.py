#!/usr/bin/env python3
"""Apply policy.yml and exceptions.yml to a licence scan.

Usage:
  policy.py --repo selfxyz/x --workspace apps/web --profile backend --visibility private --mode report \
            --checkout DIR --head head/deps.csv --head-ref SHA \
            [--base base/deps.csv --base-ref SHA] [--config DIR/.github/compliance.json] \
            --out findings.json --summary summary.md

Exceptions come from two places, checked in this order:
  1. exceptions.yml in selfxyz/compliance (org-wide, reviewed by its CODEOWNERS)
  2. `exceptions` in the scanned repo's .github/compliance.json (repo-level, reviewed in that repo)
Both are listed in the summary, so nothing is excepted silently.

With --base (PRs and pushes), only findings the change adds are new; findings that were
already there are listed as existing and never fail the run.
Exit code: 1 only in block mode when there is a new, non-excepted `fail`.
"""
import argparse, csv, datetime, fnmatch, json, os, re, subprocess, sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ORDER = {"pass": 0, "flag": 1, "fail": 2}


def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f) or {}


def action_for(action, key):
    if isinstance(action, dict):
        return action.get(key, action.get("default", "pass"))
    return action


def dep_findings(csv_path, rules, profile):
    out = []
    if not csv_path:
        return out
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            lic, cat, scope = row["licence"] or "", row["category"], row["scope"]
            for rule in rules:
                if "category" in rule and rule["category"] != cat:
                    continue
                if "scope" in rule and rule["scope"] != scope:
                    continue
                if "licence" in rule and not re.search(rule["licence"], lic, re.I):
                    continue
                act = action_for(rule["action"], profile)
                if act != "pass":
                    out.append(dict(kind="dependency", rule=rule["id"], action=act,
                                    ecosystem=row["ecosystem"], name=row["name"], version=row["version"],
                                    licence=lic or "(none)", scope=scope, ships=row.get("ships", ""),
                                    note=row.get("note", ""), why=rule.get("why", "")))
                break
    return out


def file_findings(checkout, ref, files_cfg, visibility):
    if not checkout or not ref:
        return []
    out = []
    listed = subprocess.run(["git", "-C", checkout, "ls-tree", "-r", "--name-only", ref],
                            capture_output=True, text=True, check=True).stdout.split("\n")
    listed = [p for p in listed if p and not any(fnmatch.fnmatch(p, g) for g in files_cfg.get("ignore_paths", []))]
    for kind in ("fonts", "binaries"):
        cfg = files_cfg[kind]
        for p in listed:
            if any(fnmatch.fnmatch(os.path.basename(p), g) for g in cfg["glob"]):
                out.append(dict(kind="file", rule=kind, action=cfg["action"], name=p, licence="", why=cfg["why"]))
    if not any(re.match(r"(?i)^(LICEN[CS]E|COPYING)[^/]*$", p) for p in listed):
        cfg = files_cfg["licence_file"]
        out.append(dict(kind="file", rule="licence_file", action=action_for(cfg["action"], visibility),
                        name="LICENSE", licence="", why=cfg["why"]))
    return out


def key(f):
    return (f["kind"], f["rule"], f.get("ecosystem", ""), f["name"], f.get("licence", ""))


def apply_exceptions(findings, exceptions, repo, today):
    for f in findings:
        for e in exceptions:
            if not any(fnmatch.fnmatch(repo, r) for r in e.get("repos", ["*"])):
                continue
            if not fnmatch.fnmatchcase(f["name"], e["package"]):
                continue
            if e.get("licence") and not re.search(e["licence"], f.get("licence", ""), re.I):
                continue
            if e.get("rule") and e["rule"] != f["rule"]:
                continue
            expires = str(e.get("expires") or "")
            if expires and expires < today:
                f["exception_expired"] = e["id"]
                continue
            f["excepted"], f["exception_reason"] = e["id"], " ".join(str(e.get("reason", "")).split())
            break
    return findings


def summary_md(a, new, existing, excepted, blocking):
    L = [f"## Licence and file checks: `{a.repo}`" + (f" / `{a.workspace}`" if a.workspace else ""), "",
         f"Profile **{a.profile}**, {a.visibility} repo, mode **{a.mode}**.", ""]
    counts = lambda fs, act: sum(1 for f in fs if f["action"] == act)
    label = "New in this change" if a.base else "Findings"
    L += ["| | fail | flag |", "|---|---|---|",
          f"| {label} | {counts(new, 'fail')} | {counts(new, 'flag')} |"]
    if a.base:
        L.append(f"| Already there before this change | {counts(existing, 'fail')} | {counts(existing, 'flag')} |")
    L += [f"| Covered by an exception | {counts(excepted, 'fail')} | {counts(excepted, 'flag')} |", ""]
    if blocking:
        L += ["**Blocking:** a `fail` finding without an exception. Remove the dependency, or add an exception "
              "with a reason to `exceptions` in this repo's `.github/compliance.json`.", ""]
    elif a.mode == "report" and any(f["action"] == "fail" for f in new):
        L += ["Report mode: these would block once this repo moves to block mode.", ""]
    rows = sorted(new, key=lambda f: (-ORDER[f["action"]], f["rule"], f["name"]))
    if rows:
        L += [f"### {label}", "", "| Action | Rule | Package / file | Licence | Scope | Why |", "|---|---|---|---|---|---|"]
        for f in rows[:60]:
            exp = f" (exception {f['exception_expired']} expired)" if f.get("exception_expired") else ""
            name = f"{f['name']} {f.get('version', '')}".strip()
            L.append(f"| {f['action']} | {f['rule']} | `{name}`{exp} | {f.get('licence', '')} | {f.get('scope', '')} | {f['why'].split('. ')[0].rstrip('.')}. |")
        if len(rows) > 60:
            L.append(f"| | | ... {len(rows) - 60} more in the findings artifact | | | |")
        L.append("")
    if excepted:
        L += ["### Covered by exceptions", "", "| Package / file | Rule | Exception | Reason |", "|---|---|---|---|"]
        L += [f"| `{f['name']}` | {f['rule']} | `{f['excepted']}` | {f['exception_reason']} |" for f in excepted[:30]] + [""]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    for k in ("repo", "workspace", "profile", "visibility", "mode", "head", "base", "checkout", "head-ref", "base-ref", "config", "out", "summary"):
        ap.add_argument("--" + k, default="")
    ap.add_argument("--exceptions", default=os.path.join(HERE, "..", "exceptions.yml"))
    a = ap.parse_args()

    pol = load_yaml(os.path.join(HERE, "policy.yml"))
    if a.profile not in pol["profiles"]:
        sys.exit(f"unknown profile {a.profile!r}; expected one of {pol['profiles']}")
    if not os.path.exists(a.head):
        sys.exit(f"no scan result at {a.head!r}: the scanner did not run or failed")
    exceptions = (load_yaml(a.exceptions).get("exceptions") or []) if os.path.exists(a.exceptions) else []
    if a.config and os.path.exists(a.config):  # repo-level exceptions; the schema is enforced by plan.py
        with open(a.config) as f:
            local = json.load(f).get("exceptions") or []
        exceptions += [{**e, "id": f"repo:{e.get('id') or i + 1}", "repos": [a.repo]} for i, e in enumerate(local)]
    today = datetime.date.today().isoformat()

    head = dep_findings(a.head, pol["rules"], a.profile) + file_findings(a.checkout, a.head_ref or "HEAD", pol["files"], a.visibility)
    head = apply_exceptions(head, exceptions, a.repo, today)
    base = dep_findings(a.base, pol["rules"], a.profile) + file_findings(a.checkout, a.base_ref, pol["files"], a.visibility) if a.base else []
    base_keys = {key(f) for f in base}

    excepted = [f for f in head if f.get("excepted")]
    live = [f for f in head if not f.get("excepted")]
    new = [f for f in live if key(f) not in base_keys]
    existing = [f for f in live if f not in new]
    blocking = a.mode == "block" and any(f["action"] == "fail" for f in new)

    result = dict(repo=a.repo, workspace=a.workspace, profile=a.profile, mode=a.mode, generated=today, pr=bool(a.base),
                  blocking=blocking, new=new, existing=existing, excepted=excepted)
    if a.out:
        with open(a.out, "w") as f:
            json.dump(result, f, indent=1)
    md = summary_md(a, new, existing, excepted, blocking)
    if a.summary:
        with open(a.summary, "w") as f:
            f.write(md)
    print(md)
    sys.exit(1 if blocking else 0)


if __name__ == "__main__":
    main()
