"""bin/plan.py on the fixture monorepo: the plan, changed workspaces, kind mismatches, no config."""
import json, os

from helpers import ROOT, args_of, cfg, commit, fixture_config, main, plan, repo


def test_full_plan():
    d, _ = repo("full", fixture=True)
    rc, p, _ = plan(d)
    assert rc == 0
    assert {m["path"]: m["profile"] for m in p["matrix"]} == {
        "apps/mobile": "distributed", "apps/web": "web", "packages/sdk": "distributed",
        "services/api": "backend", "contracts": "contracts"}, p["matrix"]  # examples is excluded
    assert args_of(p, "apps/mobile") == ["--path", "apps/mobile", "--npm-importer", "apps/mobile",
                                         "--podfile-lock", "apps/mobile/ios/Podfile.lock",
                                         "--gradle-files", "apps/mobile/android/app/build.gradle"]
    assert args_of(p, "services/api")[2:] == ["--cargo-lock", "services/api/Cargo.lock"]
    assert args_of(p, "contracts")[2:] == ["--solidity", "contracts/src"]
    assert [m["file_checks"] for m in p["matrix"]] == [True, False, False, False, False]


def test_sdk_only_pr():
    d, base = repo("sdk-pr", fixture=True)
    commit(d, {"packages/sdk/package.json": {"name": "@selfxyz/sdk", "version": "1.0.1", "main": "dist/index.js", "files": ["dist"]}})
    rc, p, _ = plan(d, base)
    assert rc == 0 and [m["path"] for m in p["matrix"]] == ["packages/sdk"], p


def test_readme_only_pr():
    d, base = repo("readme-pr", fixture=True)
    commit(d, {"README.md": "changed\n"})
    rc, p, _ = plan(d, base)
    assert rc == 0 and p["matrix"] == []


def test_pnpm_lock_only_change():
    d, base = repo("pnpm-lock", fixture=True)
    lock = open(os.path.join(d, "pnpm-lock.yaml")).read().replace("sha512-y", "sha512-z")
    commit(d, {"pnpm-lock.yaml": lock})
    rc, p, _ = plan(d, base)
    assert rc == 0 and [m["path"] for m in p["matrix"]] == ["apps/mobile", "apps/web", "packages/sdk"], p


def test_new_workspace_not_on_base():
    d, base = repo("new-ws", fixture=True)
    c = fixture_config(d)
    c["workspaces"].append({"path": "services/new", "kind": "service"})
    commit(d, {".github/compliance.json": c, "services/new/Cargo.lock": "version = 3\n",
               "services/new/Cargo.toml": '[package]\nname = "new"\nversion = "0.1.0"\n'})
    rc, p, _ = plan(d, base)
    assert rc == 0 and "services/new" in [m["path"] for m in p["matrix"]]
    assert len(p["matrix"]) == 6, "a config change re-plans every workspace"


def test_sdk_declared_service_fails():
    d, _ = repo("sdk-service", fixture=True)
    c = fixture_config(d)
    c["workspaces"][2]["kind"] = "service"
    commit(d, {".github/compliance.json": c})
    rc, _, out = plan(d)
    assert rc == 2 and "packages/sdk/package.json shows it is npm-package" in out, out


def test_ios_declared_web_fails():
    d, _ = repo("ios-web", fixture=True)
    c = fixture_config(d)
    c["workspaces"][0]["kind"] = "web-app"
    commit(d, {".github/compliance.json": c})
    rc, _, out = plan(d)
    assert rc == 2 and "Podfile.lock shows it is ios-app" in out, out


def test_invalid_config_fails():
    d, _ = repo("invalid", {".github/compliance.json": {"version": 2, "kind": "spaceship"}})
    rc, _, out = plan(d)
    assert rc == 2 and "::error title=compliance.json::" in out, out


def test_missing_workspace_path_fails():
    d, _ = repo("missing", {"package.json": {"name": "x", "private": True},
                            ".github/compliance.json": cfg(workspaces=[{"path": "does/not/exist", "kind": "service"}])})
    rc, _, out = plan(d)
    assert rc == 2 and "does not exist at this commit" in out, out


def test_duplicate_workspace_path_fails():
    d, _ = repo("dup", {"package.json": {"name": "x", "private": True},
                        ".github/compliance.json": cfg(workspaces=[{"path": ".", "kind": "service"}, {"path": "./", "kind": "web-app"}])})
    rc, _, out = plan(d)
    assert rc == 2 and "listed twice" in out, out


def test_no_config_autodetects():
    d, _ = repo("noconfig", fixture=True)
    commit(d, {".github/compliance.json": None})
    rc, p, _ = plan(d)
    assert rc == 0 and p["unconfigured"] and p["mode"] == "report"
    kinds = {m["path"]: m["kinds"][0] for m in p["matrix"]}
    assert kinds["apps/mobile/ios"] == "ios-app" and kinds["packages/sdk"] == "npm-package" and kinds["services/api"] == "service", kinds


def test_detected_workspaces_pass_as_a_config():
    """What detection finds must be a valid config: write it back as compliance.json and plan again."""
    files = {"ios/App.xcodeproj/project.pbxproj": "x", "sdk/Lib.podspec": "x", "web/package.json": {"name": "w", "private": True},
             "svc/Cargo.toml": '[package]\nname = "s"\n', "svc/Cargo.lock": "version = 3\n"}
    d, _ = repo("roundtrip", files)
    rc, p, _ = plan(d)
    found = {m["path"]: m["kinds"] for m in p["matrix"]}
    assert rc == 0 and found == {"ios": ["ios-app"], "sdk": ["native-sdk"], "svc": ["service"], "web": ["web-app"]}, found
    commit(d, {".github/compliance.json": cfg(mode="block", workspaces=[{"path": k, "kind": v} for k, v in found.items()])})
    rc, p, out = plan(d)
    assert rc == 0 and p["mode"] == "block" and not p["unconfigured"], out


def test_no_config_and_nothing_recognisable_still_plans():
    d, _ = repo("empty", {"README.md": "x"})
    rc, p, _ = plan(d)
    assert rc == 0 and p["unconfigured"] and p["matrix"] == [] and p["excluded"] == [{"path": ".", "kinds": ["tooling"]}], p


def test_schema_and_kinds_agree():
    import yaml
    kinds = set(yaml.safe_load(open(os.path.join(ROOT, "policy", "kinds.yml")))["kinds"])
    schema = set(json.load(open(os.path.join(ROOT, "schema", "compliance.schema.json")))["$defs"]["kind"]["enum"])
    assert kinds == schema, kinds ^ schema


def test_shipped_configs_validate():
    import glob, jsonschema
    schema = json.load(open(os.path.join(ROOT, "schema", "compliance.schema.json")))
    shipped = [f for pat in ("examples/*.json", "template/.github/compliance.json") for f in glob.glob(os.path.join(ROOT, pat))]
    assert len(shipped) >= 6
    for f in shipped:
        errors = [e.message for e in jsonschema.Draft202012Validator(schema).iter_errors(json.load(open(f)))]
        assert not errors, (f, errors)

if __name__ == "__main__":
    main(globals())
