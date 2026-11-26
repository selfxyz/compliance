"""Scanner flags for one workspace: which lockfiles it owns, and what the scanner cannot read."""
import fnmatch, os, re

import yaml

from config import owner_of, within

# Dependency manifests the scanner cannot read. A workspace holding one gets a warning in the summary.
UNSCANNED = [
    (r"(^|/)bun\.lockb$", "bun.lockb (binary lockfile; commit the text bun.lock)"),
    (r"(^|/)(requirements[^/]*\.txt|pyproject\.toml|poetry\.lock|uv\.lock)$", "Python dependencies"),
    (r"(^|/)go\.(mod|sum)$", "Go modules"),
    (r"(^|/)gradle\.lockfile$", "Gradle lockfile"),
]


def pnpm_importers(repo, files, ignore):
    """Importers of the root pnpm-lock.yaml. A single-package lock has no importers section: the root is the only one."""
    if "pnpm-lock.yaml" not in files:
        return []
    with open(os.path.join(repo, "pnpm-lock.yaml")) as f:
        docs = [d for d in yaml.safe_load_all(f) if d]
    importers = (docs[-1] if docs else {}).get("importers") or {".": {}}
    return [i for i in importers if not any(fnmatch.fnmatch(i, g) or fnmatch.fnmatch(i + "/", g) for g in ignore)]


def nearest(files, name, ws_path):
    """The `name` file in the closest folder at or above ws_path, else the first one inside it."""
    above = [f for f in files if os.path.basename(f) == name and within(ws_path, os.path.dirname(f) or ".")]
    if above:
        return max(above, key=len)
    inside = sorted(f for f in files if os.path.basename(f) == name and within(f, ws_path))
    return inside[0] if inside else None


def scan_args(repo, ws, files, workspaces, importers):
    """Scanner flags for the lockfiles this workspace owns, plus warnings for what cannot be scanned."""
    own = [f for f in files if owner_of(f, workspaces) is ws]
    pick = lambda rx: sorted(f for f in own if re.search(rx, f))
    args, warn = ["--path", ws["path"]], []
    mine = [i for i in importers if (ws["path"] == "." if i == "." else owner_of(i, workspaces) is ws)]
    if mine:
        args += ["--npm-importer", ",".join(sorted(mine))]
    if (l := pick(r"(^|/)package-lock\.json$")): args += ["--npm-lock", ",".join(l)]
    pkgs = pick(r"(^|/)package\.json$")
    ylock = nearest(files, "yarn.lock", ws["path"])
    if ylock and pkgs:
        ydir = os.path.dirname(ylock) or "."
        with open(os.path.join(repo, ylock)) as f:
            berry = "__metadata:" in f.read(2000)
        if ydir != "." and berry:
            warn.append(f"{ylock}: a yarn berry lockfile below the repo root is not supported")
        else:
            # Every package folder, not the workspace folder: the scanner reads <folder>/package.json for runtime roots.
            dirs = [os.path.dirname(f) or "." for f in pkgs if within(f, ydir)]
            args += ["--yarn-lock", f"{ylock}:{','.join(dirs)}"]
    block = nearest(files, "bun.lock", ws["path"])
    if block and pkgs:
        bdir = os.path.dirname(block) or "."
        rel = lambda f: os.path.relpath(os.path.dirname(f) or ".", bdir)   # bun names workspaces relative to the lockfile
        args += ["--bun-lock", f"{block}:{','.join(rel(f) for f in pkgs if within(f, bdir))}"]
    if (l := pick(r"(^|/)Podfile\.lock$")):
        args += ["--podfile-lock", l[0]]
        warn += [f"{f}: only one Podfile.lock per workspace is scanned" for f in l[1:]]
    if (l := pick(r"\.podspec$")): args += ["--podspec", ",".join(l)]
    if (l := pick(r"(^|/)Package\.(swift|resolved)$")): args += ["--swift", ",".join(l)]
    if (l := pick(r"(^|/)build\.gradle(\.kts)?$")): args += ["--gradle-files", ",".join(l)]
    if (l := pick(r"(^|/)gradle/libs\.versions\.toml$")): args += ["--version-catalog", l[0]]
    if (l := pick(r"(^|/)Cargo\.lock$")): args += ["--cargo-lock", ",".join(l)]
    sol = sorted({os.path.dirname(f) for f in own if f.endswith(".sol") and not re.search(r"(^|/)(lib|test|script)/", f)})
    if sol: args += ["--solidity", ",".join(sol)]
    circ = sorted({os.path.dirname(f) for f in own if f.endswith(".circom") and not re.search(r"(^|/)test/", f)})
    if circ: args += ["--circom", ",".join(circ)]

    js = {"--npm-importer", "--npm-lock", "--yarn-lock", "--bun-lock"} & set(args)
    if pkgs and not js and not any("yarn" in w for w in warn):
        warn.append(f"{pkgs[0]}: no pnpm-lock.yaml, package-lock.json, yarn.lock or bun.lock covers it, so its npm dependencies are not scanned")
    if (l := pick(r"(^|/)pnpm-lock\.yaml$")) and l != ["pnpm-lock.yaml"]:
        warn.append(f"{l[0]}: only a pnpm-lock.yaml at the repo root is scanned")
    if pick(r"(^|/)Cargo\.toml$") and "--cargo-lock" not in args:
        warn.append("Cargo.toml without a Cargo.lock: Rust dependencies are not scanned")
    for rx, label in UNSCANNED:
        if (l := pick(rx)) and not (label.startswith("bun.lockb") and js):
            warn.append(f"{l[0]}: {label} not supported by the scanner")
    return args, warn
