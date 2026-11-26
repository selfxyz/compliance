"""Shared by the test files: throwaway git repos, running plan.py, and a tiny test runner."""
import json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURE = os.path.join(HERE, "fixture-monorepo")
TMP = tempfile.mkdtemp(prefix="compliance-test-")
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
ENV = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_")}   # tests must not write to a real job summary


def commit(repo_dir, changes=None):
    """Write {path: text | dict (as JSON) | None (delete)}, commit everything, return the sha."""
    for path, text in (changes or {}).items():
        full = os.path.join(repo_dir, path)
        if text is None:
            os.remove(full)
            continue
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(text if isinstance(text, str) else json.dumps(text))
    subprocess.run(GIT + ["-C", repo_dir, "add", "-A"], check=True)
    subprocess.run(GIT + ["-C", repo_dir, "commit", "-qm", "x", "--allow-empty"], check=True)
    return subprocess.run(["git", "-C", repo_dir, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def repo(name, files=None, fixture=False):
    """A new git repo (a copy of the fixture, or empty) with `files` added. Returns (dir, sha). No `origin` remote."""
    d = os.path.join(TMP, name)
    shutil.copytree(FIXTURE, d) if fixture else os.makedirs(d)
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    return d, commit(d, files)


def plan(d, base=None, env=None):
    """Run bin/plan.py (full unless `base` is given). Returns (exit code, plan dict or None, output)."""
    out = os.path.join(d, ".plan.json")
    args = [sys.executable, os.path.join(ROOT, "bin", "plan.py"), "--repo", d, "--out", out] + (["--base", base] if base else ["--full"])
    r = subprocess.run(args, capture_output=True, text=True, env={**ENV, **(env or {})})
    return r.returncode, (json.load(open(out)) if r.returncode == 0 else None), r.stdout + r.stderr


def cfg(**kw):
    return {"version": 1, **kw}


def args_of(p, path):
    return next(m["scan_args"] for m in p["matrix"] if m["path"] == path)


def fixture_config(d):
    return json.load(open(os.path.join(d, ".github", "compliance.json")))


def main(tests):
    """Run every test_* function in `tests` (a module's globals()); exit 1 if any fails."""
    failed = 0
    for name, fn in sorted((k, v) for k, v in tests.items() if k.startswith("test_") and callable(v)):
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:
            failed += 1
            detail = e.stderr[-400:] if isinstance(e, subprocess.CalledProcessError) and e.stderr else str(e)[:400]
            print(f"FAIL {name}: {type(e).__name__} {detail}")
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if failed else 0)
