"""What a repo can change for itself in compliance.json, and that the job summary lists all of it."""
import os

from helpers import cfg, commit, fixture_config, main, plan, repo


def test_folder_left_out_of_workspaces_fails():
    d, _ = repo("left-out", fixture=True)
    c = fixture_config(d)
    c["workspaces"] = [w for w in c["workspaces"] if w["path"] != "packages/sdk"]
    commit(d, {".github/compliance.json": c})
    rc, _, out = plan(d)
    assert rc == 2 and "not covered by any workspace" in out and "packages/sdk" in out, out


def test_ignore_is_the_explicit_way_to_leave_a_folder_out():
    d, _ = repo("ignored", fixture=True)
    c = fixture_config(d)
    c["workspaces"] = [w for w in c["workspaces"] if w["path"] != "packages/sdk"]
    c["ignore"] = ["packages/sdk/*"]
    commit(d, {".github/compliance.json": c})
    rc, p, _ = plan(d)
    assert rc == 0 and p["ignore"] == ["packages/sdk/*"] and "packages/sdk" not in [m["path"] for m in p["matrix"]], p


def test_excluded_kind_is_not_cross_checked_but_is_listed():
    d, _ = repo("test-app", fixture=True)
    c = fixture_config(d)
    c["workspaces"][0]["kind"] = "test-app"   # a folder with Podfile.lock and AndroidManifest.xml
    commit(d, {".github/compliance.json": c})
    rc, p, _ = plan(d)
    assert rc == 0 and {"path": "apps/mobile", "kinds": ["test-app"]} in p["excluded"], p
    assert "apps/mobile" not in [m["path"] for m in p["matrix"]]


def test_override_is_reported():
    d, _ = repo("override", fixture=True)
    c = fixture_config(d)
    c["workspaces"][2].update(kind="service", override={"profile": "backend", "reason": "internal only, never published to npm"})
    commit(d, {".github/compliance.json": c})
    rc, p, out = plan(d)
    assert rc == 0 and "::warning title=Profile override::packages/sdk" in out
    assert p["overrides"] == [{"path": "packages/sdk", "profile": "backend", "reason": "internal only, never published to npm"}], p.get("overrides")


def test_override_to_excluded():
    d, _ = repo("override-excluded", fixture=True)
    c = fixture_config(d)
    c["workspaces"][2]["override"] = {"profile": "excluded", "reason": "prototype, removed before the first release"}
    commit(d, {".github/compliance.json": c})
    rc, p, _ = plan(d)
    assert rc == 0 and "packages/sdk" not in [m["path"] for m in p["matrix"]]
    assert p["overrides"][0]["profile"] == "excluded"


def test_main_alone_is_not_publishable():
    d, _ = repo("main-only", {"package.json": {"name": "my-api", "version": "1.0.0", "main": "index.js"},
                              "package-lock.json": {"lockfileVersion": 3, "packages": {}},
                              ".github/compliance.json": cfg(kind="service")})
    rc, p, _ = plan(d)
    assert rc == 0 and p["matrix"][0]["profile"] == "backend", p


def test_repo_level_exceptions_validate():
    d, _ = repo("exceptions", fixture=True)
    c = fixture_config(d)
    c["exceptions"] = [{"package": "some-gpl-lib", "rule": "gpl", "reason": "replaced in the next release, tracked in SELF-123"}]
    commit(d, {".github/compliance.json": c})
    rc, p, _ = plan(d)
    assert rc == 0 and p["exceptions"] == 1
    c["exceptions"] = [{"package": "some-gpl-lib", "reason": "because"}]   # reason too short
    commit(d, {".github/compliance.json": c})
    assert plan(d)[0] == 2


def test_curations_are_not_a_config_field():
    d, _ = repo("curations", fixture=True)
    c = fixture_config(d)
    c["curations"] = [{"name": "ring", "licence": "MIT", "evidence": "LICENSE file in the crate tarball"}]
    commit(d, {".github/compliance.json": c})
    assert plan(d)[0] == 2


def test_summary_lists_everything_a_repo_switched_off():
    d, _ = repo("summary", fixture=True)
    c = fixture_config(d)
    c["workspaces"][2].update(kind="service", override={"profile": "backend", "reason": "internal only, never published to npm"})
    commit(d, {".github/compliance.json": c})
    summ = os.path.join(d, ".summary.md")
    plan(d, env={"GITHUB_STEP_SUMMARY": summ})
    md = open(summ).read()
    assert "### Profile overrides" in md and "never published to npm" in md and "### Not scanned" in md and "`examples`" in md and "### Ignored paths" in md, md

if __name__ == "__main__":
    main(globals())
