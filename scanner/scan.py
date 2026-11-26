#!/usr/bin/env python3
"""Licence scan of one workspace at one git ref. Run it from inside the repo to scan.

Usage: scan.py REF --path DIR [lockfile flags] --out DIR --csv FILE [--offline]

Reads every file at REF with git. Writes <out>/sbom.spdx.json and the CSV that policy/policy.py
reads. It lists licences; it never decides pass or fail. bin/scanargs.py builds the lockfile flags.
"""
import argparse, csv, fnmatch, json, os, re, subprocess, sys

from apple import pod_rows, podspec_rows, swift_rows
from gradle import gradle_rows
from licences import classify
from locks import bunlock_rows, yarnlock_rows
from lookup import Lookup
from pnpm import npm_rows, npmlock_rows
from sbom import spdx
from source import cargo_rows, circom_rows, solidity_rows, submodule_rows

FLAGS = {  # all take comma-separated repo paths unless noted
    "path": "the workspace folder (default: whole repo)",
    "npm-importer": "pnpm-lock.yaml importers to follow",
    "npm-lock": "package-lock.json files",
    "yarn-lock": "FILE:FOLDERS, yarn.lock and the package folders whose dependencies are runtime roots",
    "bun-lock": "FILE:FOLDERS, bun.lock and the workspace folders (relative to it) that are runtime roots",
    "podfile-lock": "one Podfile.lock",
    "podspec": "podspec files",
    "swift": "Package.swift and Package.resolved files",
    "gradle-files": "build.gradle / build.gradle.kts files",
    "version-catalog": "one gradle/libs.versions.toml",
    "cargo-lock": "Cargo.lock files",
    "solidity": "folders of .sol sources",
    "circom": "folders of .circom sources",
    "first-party": "name prefixes never sent to outside services",
    "curations": "JSON file of hand-established licences and ships / note answers",
    "out": "output folder",
    "csv": "CSV file to write",
}
COLUMNS = ["ecosystem", "name", "version", "licence", "licence_source", "scope", "direct", "category", "ships", "note"]


def curate(rows, a):
    """Apply the repo's curations (first match wins) and set each row's category."""
    curs = []
    if a.curations and os.path.exists(a.curations):
        with open(a.curations) as f:
            curs = json.load(f)
    part = "all" if a.path in ("", ".") else os.path.basename(a.path)
    for r in rows:
        r["ships"], r["note"] = "", ""
        for c in curs:
            if c.get("ecosystem") not in (None, r["eco"]) or not fnmatch.fnmatchcase(r["name"], c["name"]):
                continue
            if c.get("version") and not fnmatch.fnmatchcase(r["version"], c["version"]):
                continue
            if c.get("part") and c["part"] != part:
                continue
            if "if_licence" in c and not re.search(c["if_licence"], r["licence"]):
                continue
            if c.get("licence"):
                r["licence"] = c["licence"]
                r["source"] = ("first-party; " if r["source"].startswith("first-party") else "") + "curation: " + c.get("evidence", "")
            r["ships"], r["note"] = c.get("ships", ""), c.get("note", "")
            break
        r["cat"] = "first-party" if r["source"].startswith("first-party") else classify(r["licence"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ref")
    for flag, text in FLAGS.items():
        ap.add_argument("--" + flag, default="", help=text)
    ap.add_argument("--offline", action="store_true", help="no licence lookups, cache only")
    a = ap.parse_args()
    a.repo, a.importer = os.getcwd(), a.npm_importer
    git = lambda *args: subprocess.run(["git", "-C", a.repo, *args], capture_output=True, text=True)
    rev = git("rev-parse", "--verify", a.ref + "^{commit}")
    if rev.returncode:
        sys.exit(f"not a commit in this repo: {a.ref}")
    a.sha = rev.stdout.strip()
    origin = git("remote", "get-url", "origin").stdout.strip()
    a.repo_name = re.sub(r"\.git$", "", origin.rsplit("/", 1)[-1]) or os.path.basename(git("rev-parse", "--show-toplevel").stdout.strip())

    lk = Lookup(a.offline)
    fps = [x for x in (a.first_party or "@selfxyz/,selfxyz,xyz.self").split(",") if x]
    nrows, _ = npm_rows(a, lk, fps)
    npm_lic = {(r["name"], r["version"]): r["licence"] for r in nrows}
    rows = (nrows + npmlock_rows(a, lk, fps) + yarnlock_rows(a, lk, fps) + bunlock_rows(a, lk, fps)
            + pod_rows(a, npm_lic, lk, fps) + podspec_rows(a, lk, fps) + gradle_rows(a, lk, npm_lic, fps) + swift_rows(a, lk, fps)
            + cargo_rows(a, lk, fps) + solidity_rows(a) + circom_rows(a, nrows) + submodule_rows(a, lk, fps))
    curate(rows, a)

    os.makedirs(a.out, exist_ok=True)
    with open(a.csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        w.writerows([r["eco"], r["name"], r["version"], r["licence"], r["source"], r["scope"], r["direct"], r["cat"], r["ships"], r["note"]] for r in rows)
    spdx(rows, a, a.out)
    by = {}
    for r in rows:
        by[r["cat"]] = by.get(r["cat"], 0) + 1
    print(f"{a.repo_name}/{a.path or '.'} @ {a.sha[:9]}: {len(rows)} dependencies ({', '.join(f'{k}: {v}' for k, v in sorted(by.items())) or 'none'})")
    print(f"  licence lookups: {lk.calls} requests, {lk.failed} packages with no reachable metadata")


if __name__ == "__main__":
    main()
