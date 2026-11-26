#!/usr/bin/env python3
"""Turn a repo's .github/compliance.json into the list of workspace scans for this run.

Steps:
  1. load .github/compliance.json and validate it against schema/compliance.schema.json,
     or detect workspaces when the file is missing (report mode, flagged as unconfigured)
  2. resolve each workspace's kind(s) to a licence profile with policy/kinds.yml
  3. cross-check: a folder declared as a less strict kind than its files show is an error,
     and so is a dependency manifest that no workspace covers
  4. keep only the workspaces this event touched (all of them on weekly and manual runs)
  5. build the scanner arguments for each workspace's lockfiles

Usage: plan.py --repo DIR [--base SHA --head SHA] [--full] [--out plan.json]
Writes plan.json. Under GitHub Actions it also writes the outputs `matrix`, `mode`, `any`,
`branch_rules` and the job summary. Exit code 2 on an invalid config, a kind mismatch or an
uncovered folder.
"""
import argparse, fnmatch, json, os, re, subprocess

import yaml

from config import CONFIG, ROOT, cross_check, fail, load, owner_of, resolve_profile, uncovered
from scanargs import pnpm_importers, scan_args

SKIP = re.compile(r"(^|/)(node_modules|\.git|Pods|build|vendor)/|(^|/)lib/forge-std(/|$)")

# Files whose change means the licence scan of the workspace holding them must re-run.
DEP_FILE = re.compile(r"""(^|/)(package\.json|package-lock\.json|pnpm-lock\.yaml|pnpm-workspace\.yaml|yarn\.lock|bun\.lockb?|
    Cargo\.toml|Cargo\.lock|Podfile|Podfile\.lock|[^/]+\.podspec|Package\.swift|Package\.resolved|
    build\.gradle(\.kts)?|settings\.gradle(\.kts)?|gradle\.lockfile|libs\.versions\.toml|go\.mod|go\.sum|
    requirements[^/]*\.txt|pyproject\.toml|poetry\.lock|uv\.lock|foundry\.toml|remappings\.txt|\.gitmodules|
    Dockerfile[^/]*|LICEN[CS]E[^/]*)$|\.(sol|circom|otf|ttf|woff2?|jar|aar|so|dylib|a|dll|exe)$""", re.X)
SHARED_LOCKS = ("pnpm-lock.yaml", "pnpm-workspace.yaml", "yarn.lock", "bun.lock")  # at the repo root: every JS workspace depends on them


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout


def changed(repo, base, head, workspaces, full):
    """Workspace paths this change touched, or None for "scan everything"."""
    if full or not base or set(base) == {"0"}:
        return None
    try:
        paths = git(repo, "diff", "--name-only", f"{base}...{head}").splitlines()
    except subprocess.CalledProcessError:
        return None  # base is unknown here (force push): scan everything
    if CONFIG in paths:
        return None  # config changed: re-plan everything
    hit = set()
    for p in paths:
        if not DEP_FILE.search(p):
            continue
        if p in SHARED_LOCKS:
            hit |= {w["path"] for w in workspaces if {"--npm-importer", "--yarn-lock", "--bun-lock"} & set(w["scan_args"])}
            continue
        w = owner_of(p, workspaces)
        if w:
            hit.add(w["path"])
    print(f"{len(paths)} changed file(s); workspaces to scan: {sorted(hit) or 'none'}")
    return hit


def summary_md(p):
    L = [f"## Compliance plan: mode {p['mode']}", ""]
    if p["unconfigured"]:
        L += [f"No `{CONFIG}`: workspaces were auto-detected and everything is report-only.", ""]
    L += ["| Workspace | Kind | Rules |", "|---|---|---|"]
    L += [f"| `{m['path']}` | {', '.join(m['kinds'])} | {m['profile']} |" for m in p["matrix"]]
    if not p["matrix"]:
        L.append("| (none: no dependency files changed) | | |")
    # Everything a repo switched off or changed for itself is listed.
    if p["overrides"]:
        L += ["", "### Profile overrides", "", "| Workspace | Rules used | Reason |", "|---|---|---|"]
        L += [f"| `{o['path']}` | {o['profile']} | {o['reason']} |" for o in p["overrides"]]
    if p["excluded"]:
        L += ["", "### Not scanned (tooling, examples, test apps)", ""] + [f"- `{e['path']}` ({', '.join(e['kinds'])})" for e in p["excluded"]]
    if p["ignore"]:
        L += ["", "### Ignored paths", ""] + [f"- `{g}`" for g in p["ignore"]]
    if p["exceptions"]:
        L += ["", f"### Repo-level exceptions: {p['exceptions']}", "", "Listed with their reasons in each licence job that uses one."]
    if p["warnings"]:
        L += ["", "### Not covered by the scanner", ""] + [f"- `{w['path']}`: {w['message']}" for w in p["warnings"]]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--base", default="")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--out", default="plan.json")
    a = ap.parse_args()

    with open(os.path.join(ROOT, "policy", "kinds.yml")) as f:
        kinds_cfg = yaml.safe_load(f)
    files = [f for f in git(a.repo, "ls-files").split("\n") if f and not SKIP.search(f)]
    cfg, unconfigured = load(a.repo, kinds_cfg, files)
    ignore = cfg.get("ignore", [])
    files = [f for f in files if not any(fnmatch.fnmatch(f, g) for g in ignore)]
    workspaces = cfg["workspaces"]

    importers = pnpm_importers(a.repo, files, ignore)

    for ws in workspaces:
        ws["profile"] = resolve_profile(ws, kinds_cfg)
    if not unconfigured and (loose := uncovered(a.repo, workspaces, kinds_cfg, files, importers)):
        fail(f"not covered by any workspace: {', '.join(loose[:5])}{' ...' if len(loose) > 5 else ''}. "
             f"Add a workspace for it (kind tooling, example or test-app if it never ships), or list it in `ignore`.")
    warnings = []
    for ws in workspaces:
        if not unconfigured:  # detected kinds already come from the evidence
            cross_check(a.repo, ws, workspaces, kinds_cfg, files)
        ws["scan_args"], warn = scan_args(a.repo, ws, files, workspaces, importers)
        if ws["profile"] != "excluded":
            warnings += [{"path": ws["path"], "message": w} for w in warn]
            print("".join(f"::warning title=Not scanned::{ws['path']}: {w}\n" for w in warn), end="")

    todo = changed(a.repo, a.base, a.head, workspaces, a.full)
    matrix = []
    for ws in workspaces:
        if ws["profile"] == "excluded" or (todo is not None and ws["path"] not in todo):
            continue
        name = ws["path"] if ws["path"] != "." else "root"
        matrix.append({"name": name, "slug": re.sub(r"[^A-Za-z0-9._-]+", "-", name), "path": ws["path"], "kinds": ws["kinds"],
                       "profile": ws["profile"], "scan_args": ws["scan_args"], "file_checks": False})
    if matrix:
        matrix[0]["file_checks"] = True  # repo-wide file checks (fonts, binaries, LICENSE) run once

    plan = {"mode": cfg.get("mode", "report"),
            "unconfigured": unconfigured,
            "matrix": matrix,
            "overrides": [{"path": w["path"], **w["override"]} for w in workspaces if w.get("override")],
            "excluded": [{"path": w["path"], "kinds": w["kinds"]} for w in workspaces if w["profile"] == "excluded" and not w.get("override")],
            "ignore": ignore, "exceptions": len(cfg.get("exceptions", [])), "warnings": warnings,
            "branch_rules": cfg.get("branch_rules", {})}
    with open(a.out, "w") as f:
        json.dump(plan, f, indent=1)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"matrix={json.dumps(matrix)}\nmode={plan['mode']}\nany={'true' if matrix else 'false'}\n"
                    f"branch_rules={json.dumps(plan['branch_rules'])}\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(summary_md(plan))
    print(json.dumps(plan, indent=1))


if __name__ == "__main__":
    main()
