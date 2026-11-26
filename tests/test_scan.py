"""End to end, offline: bin/plan.py, then bin/licences.sh (scanner + policy) per workspace, as the workflow does.
Licences come from a seeded cache, so nothing is looked up."""
import csv, json, os, subprocess

from helpers import ENV, ROOT, TMP, commit, fixture_config, main, plan, repo

CACHE = {"NPM:some-gpl-lib@1.0.0": "GPL-3.0-only", "NPM:react@18.3.1": "MIT", "CARGO:serde@1.0.210": "MIT OR Apache-2.0",
         "POD:OpenSSL-Universal@1.1.2200": "Apache-2.0", "NPM:left-pad@1.3.0": "WTFPL", "NPM:is-odd@3.0.1": "MIT",
         "NPM:is-number@6.0.0": "MIT", "NPM:typescript@5.6.0": "Apache-2.0", "NPM:agpl-thing@2.0.0": "AGPL-3.0-only"}
os.makedirs(os.path.join(TMP, "cache", "oss-scan"))
with open(os.path.join(TMP, "cache", "oss-scan", "licences.json"), "w") as f:
    json.dump({k: {"lic": v, "src": "seed"} for k, v in CACHE.items()}, f)


def run(d, mode="block", base=None):
    """Plan, then check every planned workspace. Returns {workspace: (exit code, findings, csv rows)}."""
    rc, p, out = plan(d, base)
    assert rc == 0, out
    res = {}
    for m in p["matrix"]:
        o = os.path.join(d, ".scan", m["slug"])
        env = {**ENV, "XDG_CACHE_HOME": os.path.join(TMP, "cache"), "OFFLINE": "true", "HEAD": "HEAD", "BASE": base or "", "FULL": "false" if base else "true",
               "SCAN_ARGS": json.dumps(m["scan_args"]), "REPO": "selfxyz/fixture", "WORKSPACE": m["path"], "PROFILE": m["profile"],
               "VISIBILITY": "private", "MODE": mode, "FILE_CHECKS": str(m["file_checks"]).lower()}
        r = subprocess.run(["bash", os.path.join(ROOT, "bin", "licences.sh"), d, o], capture_output=True, text=True, env=env)
        assert os.path.exists(o + "/findings.json"), r.stdout + r.stderr
        res[m["path"]] = (r.returncode, json.load(open(o + "/findings.json")), list(csv.DictReader(open(o + "/head/deps.csv"))))
    return res


def names(findings, key="new"):
    return [(x["action"], x["rule"], x["name"]) for x in findings[key]]


def test_fixture_monorepo():
    res = run(repo("fixture", fixture=True)[0])
    assert sorted(res) == ["apps/mobile", "apps/web", "contracts", "packages/sdk", "services/api"]
    rc, f, _ = res["packages/sdk"]
    assert rc == 1 and names(f) == [("fail", "gpl", "some-gpl-lib")] and f["workspace"] == "packages/sdk", f   # distributed rules
    assert all(res[w][0] == 0 and not res[w][1]["blocking"] for w in res if w != "packages/sdk")
    assert [(r["name"], r["licence"], r["category"]) for r in res["services/api"][2]] == [("serde", "MIT OR Apache-2.0", "ok")]
    assert [(r["ecosystem"], r["name"]) for r in res["apps/mobile"][2]] == [("npm", "react"), ("cocoapods", "OpenSSL-Universal")]
    assert [r["ecosystem"] for r in res["contracts"][2]] == ["solidity-source"]
    assert os.path.exists(os.path.join(TMP, "fixture", ".scan", "packages-sdk", "head", "sbom.spdx.json"))


def test_report_mode_never_exits_nonzero():
    assert run(repo("report", fixture=True)[0], mode="report")["packages/sdk"][0] == 0


def test_change_only_new_findings_block():
    d, base = repo("pr", fixture=True)
    commit(d, {"packages/sdk/package.json": {"name": "@selfxyz/sdk", "version": "1.0.1", "files": ["dist"]}})   # touches the SDK, adds nothing
    res = run(d, base=base)
    assert list(res) == ["packages/sdk"]
    rc, f, _ = res["packages/sdk"]
    assert rc == 0 and f["pr"] and f["new"] == [] and names(f, "existing") == [("fail", "gpl", "some-gpl-lib")], f
    lock = open(os.path.join(d, "pnpm-lock.yaml")).read()
    lock = lock.replace("packages:\n", "packages:\n  agpl-thing@2.0.0:\n    resolution: {integrity: sha512-a}\n").replace("snapshots:\n", "snapshots:\n  agpl-thing@2.0.0: {}\n")
    lock = lock.replace("  apps/web:\n    dependencies:\n", "  apps/web:\n    dependencies:\n      agpl-thing:\n        specifier: ^2.0.0\n        version: 2.0.0\n")
    commit(d, {"pnpm-lock.yaml": lock})
    rc, f, _ = run(d, base=base)["apps/web"]
    assert rc == 1 and names(f) == [("fail", "agpl", "agpl-thing")], f   # AGPL fails in every profile


def test_workspace_that_is_new_in_the_change():
    d, base = repo("new-ws", fixture=True)
    c = fixture_config(d)
    c["workspaces"].append({"path": "services/new", "kind": "service"})
    commit(d, {".github/compliance.json": c, "services/new/Cargo.lock": "version = 3\n", "services/new/Cargo.toml": '[package]\nname = "new"\n'})
    rc, f, _ = run(d, base=base)["services/new"]
    assert rc == 0 and f["pr"] is False, f   # the base scan failed, so there is nothing to compare with


def test_repo_level_exception():
    d, _ = repo("exception", fixture=True)
    c = fixture_config(d)
    c["exceptions"] = [{"id": "gpl-lib", "package": "some-gpl-lib", "rule": "gpl", "reason": "replaced in the next release, tracked in SELF-123"}]
    commit(d, {".github/compliance.json": c})
    rc, f, _ = run(d)["packages/sdk"]
    assert rc == 0 and f["new"] == [] and f["excepted"][0]["excepted"] == "repo:gpl-lib", f


BUN_LOCK = """{
  "lockfileVersion": 1,
  "workspaces": {
    "": { "name": "root", "dependencies": { "is-odd": "^3.0.1", }, "devDependencies": { "typescript": "^5.6.0", }, },
  },
  "packages": {
    "is-number": ["is-number@6.0.0", "", {}, "sha512-x"],
    "is-odd": ["is-odd@3.0.1", "", { "dependencies": { "is-number": "^6.0.0" } }, "sha512-y"],
    "typescript": ["typescript@5.6.0", "", {}, "sha512-z"],
  }
}
"""
PNPM_V6 = "lockfileVersion: '6.0'\ndependencies:\n  left-pad:\n    specifier: ^1.3.0\n    version: 1.3.0\npackages:\n  /left-pad@1.3.0:\n    resolution: {integrity: sha512-x}\n"
YARN_V1 = '# yarn lockfile v1\n\nleft-pad@^1.3.0:\n  version "1.3.0"\n'
WEB = {".github/compliance.json": {"version": 1, "kind": "web-app"}}


def scopes(name, files):
    return {r["name"]: r["scope"] for r in run(repo(name, {**WEB, **files})[0])["."][2]}


def test_other_lockfiles():
    assert scopes("bun", {"package.json": {"name": "root", "private": True}, "bun.lock": BUN_LOCK}) == {"is-odd": "prod", "is-number": "prod", "typescript": "dev"}
    assert scopes("pnpm6", {"package.json": {"name": "root", "private": True}, "pnpm-lock.yaml": PNPM_V6}) == {"left-pad": "prod"}
    yarn = {"package.json": {"name": "root", "private": True, "workspaces": ["packages/*"]}, "yarn.lock": YARN_V1,
            "packages/a/package.json": {"name": "a", "private": True, "dependencies": {"left-pad": "^1.3.0"}}}
    assert scopes("yarn", yarn) == {"left-pad": "prod"}
    npm = {"package.json": {"name": "x", "private": True}, "package-lock.json": {"lockfileVersion": 3, "packages": {
        "node_modules/left-pad": {"version": "1.3.0", "license": "WTFPL"}, "node_modules/typescript": {"version": "5.6.0", "dev": True, "license": "Apache-2.0"}}}}
    assert scopes("npm", npm) == {"left-pad": "prod", "typescript": "dev"}


def test_curations_from_the_scanned_repo():
    cur = [{"ecosystem": "npm", "name": "some-gpl-lib", "licence": "MIT", "evidence": "LICENSE file in the tarball", "ships": "yes", "note": "checked"}]
    d, _ = repo("curated", {"scripts/oss/curations.json": cur}, fixture=True)
    rc, f, rows = run(d)["packages/sdk"]
    assert rc == 0 and f["new"] == [] and (rows[0]["licence"], rows[0]["ships"], rows[0]["note"]) == ("MIT", "yes", "checked"), rows


if __name__ == "__main__":
    main(globals())
