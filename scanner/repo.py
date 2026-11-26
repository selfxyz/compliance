"""Read the scanned repo at one commit with git. The working tree is never read."""
import subprocess


def gitshow(a, path):
    """Text of `path` at the scanned commit, or None if it is not there."""
    r = subprocess.run(["git", "-C", a.repo, "show", f"{a.sha}:{path}"], capture_output=True)
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None


def split(v):
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def gitgrep(a, pattern, paths):
    """[(path, matching line)] at the scanned commit."""
    r = subprocess.run(["git", "-C", a.repo, "grep", "-n", "-I", "-E", pattern, a.sha, "--"] + paths, capture_output=True)
    out = []
    for line in r.stdout.decode("utf-8", "replace").splitlines():
        parts = line.split(":", 3)  # sha:path:line:text
        if len(parts) == 4:
            out.append((parts[1], parts[3]))
    return out


def tree(a):
    """[(type, sha, path)] for every entry at the scanned commit. A submodule has type "commit"."""
    if not hasattr(a, "_tree"):
        out = subprocess.run(["git", "-C", a.repo, "ls-tree", "-r", a.sha], capture_output=True, text=True, check=True).stdout
        a._tree = [(meta.split()[1], meta.split()[2], name) for meta, _, name in (l.partition("\t") for l in out.splitlines())]
    return a._tree


def pnpm_lock(a):
    """The root pnpm-lock.yaml as a dict, or None. Newer pnpm writes two YAML documents; the project's is the last."""
    if not hasattr(a, "_pnpm"):
        import yaml  # only repos with a pnpm lockfile need PyYAML
        txt = gitshow(a, "pnpm-lock.yaml")
        docs = [d for d in yaml.safe_load_all(txt) if d] if txt else []
        a._pnpm = docs[-1] if docs else None
    return a._pnpm
