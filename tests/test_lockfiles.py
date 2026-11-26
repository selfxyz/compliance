"""Scanner flags per lockfile layout: yarn, npm, bun, Swift, nested workspaces, and what cannot be scanned."""
from helpers import args_of, cfg, commit, main, plan, repo

YARN_LOCK = '# yarn lockfile v1\n\nleft-pad@^1.3.0:\n  version "1.3.0"\n'
NESTED = {
    "Cargo.toml": '[package]\nname = "svc"\nversion = "0.1.0"\n', "Cargo.lock": "version = 3\n",
    "sdk/package.json": {"name": "@selfxyz/sdk", "main": "index.js", "files": ["dist"]},
    "sdk/package-lock.json": {"lockfileVersion": 3, "packages": {}},
}


def test_yarn_workspace():
    d, _ = repo("yarn", {
        "package.json": {"name": "root", "private": True, "workspaces": ["packages/*"]},
        "yarn.lock": YARN_LOCK,
        "packages/a/package.json": {"name": "a", "private": True, "dependencies": {"left-pad": "^1.3.0"}},
        "packages/b/package.json": {"name": "b", "private": True},
        ".github/compliance.json": cfg(workspaces=[{"path": "packages/a", "kind": "web-app"}, {"path": "packages/b", "kind": "web-app"}])})
    rc, p, _ = plan(d)
    assert rc == 0 and args_of(p, "packages/a")[2:] == ["--yarn-lock", "yarn.lock:packages/a"], p


def test_yarn_workspace_folder_of_packages():
    """A workspace that is a folder of packages must hand every package folder to the scanner,
    otherwise the scanner finds no package.json, no runtime roots, and marks everything dev (= pass)."""
    d, _ = repo("yarn-folder", {
        "package.json": {"name": "root", "private": True, "workspaces": ["packages/*"]},
        "yarn.lock": YARN_LOCK,
        "packages/a/package.json": {"name": "a", "private": True, "dependencies": {"left-pad": "^1.3.0"}},
        "packages/b/package.json": {"name": "b", "private": True},
        ".github/compliance.json": cfg(workspaces=[{"path": "packages", "kind": "web-app"}])})
    rc, p, _ = plan(d)
    assert rc == 0 and args_of(p, "packages")[2:] == ["--yarn-lock", "yarn.lock:packages/a,packages/b"], p


def test_yarn_lock_below_the_root():
    d, _ = repo("yarn-nested", {"Cargo.toml": '[package]\nname = "svc"\n', "Cargo.lock": "version = 3\n",
                                "sidecar/package.json": {"name": "sidecar", "private": True, "dependencies": {"left-pad": "^1.3.0"}},
                                "sidecar/yarn.lock": YARN_LOCK, ".github/compliance.json": cfg(kind="service")})
    rc, p, _ = plan(d)
    assert rc == 0 and p["matrix"][0]["scan_args"] == ["--path", ".", "--yarn-lock", "sidecar/yarn.lock:sidecar", "--cargo-lock", "Cargo.lock"], p


def test_npm_package_lock():
    d, _ = repo("npm", {
        "package.json": {"name": "svc", "private": True},
        "package-lock.json": {"lockfileVersion": 3, "packages": {}},
        ".github/compliance.json": cfg(kind="service")})
    rc, p, _ = plan(d)
    assert rc == 0 and p["matrix"][0]["scan_args"] == ["--path", ".", "--npm-lock", "package-lock.json"], p
    assert p["matrix"][0]["name"] == "root"


def test_swift_package():
    d, _ = repo("swift", {
        "Package.swift": "// swift-tools-version:5.9\n", "Package.resolved": '{"pins": [], "version": 2}',
        ".github/compliance.json": cfg(kind="native-sdk")})
    rc, p, _ = plan(d)
    assert rc == 0 and p["matrix"][0]["scan_args"] == ["--path", ".", "--swift", "Package.resolved,Package.swift"], p
    assert p["matrix"][0]["profile"] == "distributed"


def test_nested_workspaces():
    d, base = repo("nested", {**NESTED, ".github/compliance.json": cfg(workspaces=[
        {"path": ".", "kind": "service"}, {"path": "sdk", "kind": "npm-package"}])})
    rc, p, _ = plan(d)
    assert rc == 0
    assert args_of(p, ".") == ["--path", ".", "--cargo-lock", "Cargo.lock"]             # root does not take the sdk lock
    assert args_of(p, "sdk") == ["--path", "sdk", "--npm-lock", "sdk/package-lock.json"]
    commit(d, {"sdk/package-lock.json": {"lockfileVersion": 3, "packages": {}, "x": 1}})
    rc, p, _ = plan(d, base)
    assert [m["path"] for m in p["matrix"]] == ["sdk"], p                               # the inner workspace owns the change


def test_nested_undeclared_stricter_folder_fails():
    d, _ = repo("nested-undeclared", {**NESTED, ".github/compliance.json": cfg(kind="service")})
    rc, _, out = plan(d)
    assert rc == 2 and "sdk/package.json shows it is npm-package" in out, out


def test_unscannable_manifests_warn():
    d, _ = repo("warn", {"package.json": {"name": "x", "private": True}, "bun.lock": "{}", "Cargo.toml": "[package]\nname='x'\n",
                         "requirements.txt": "requests\n", ".github/compliance.json": cfg(kind="service")})
    rc, p, out = plan(d)
    msgs = " | ".join(w["message"] for w in p["warnings"])
    assert rc == 0 and "npm dependencies are not scanned" not in msgs and "--bun-lock" in p["matrix"][0]["scan_args"]
    assert "Cargo.toml without a Cargo.lock" in msgs and "Python dependencies" in msgs and "::warning title=Not scanned::" in out, msgs

if __name__ == "__main__":
    main(globals())
