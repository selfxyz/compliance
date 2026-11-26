"""The workflow files and the repo itself: gate logic, pinned actions, matching env names, file size."""
import json, os, re, subprocess, sys

import yaml

from helpers import ROOT, main

WF = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", "compliance.yml")))
TEXT = {f: open(os.path.join(ROOT, f)).read() for f in (".github/workflows/compliance.yml", ".github/workflows/test.yml", ".github/workflows/self-check.yml",
                                                        ".github/actions/setup/action.yml", "template/.github/workflows/compliance.yml")}


def gate(mode, **results):
    script = WF["jobs"]["gate"]["steps"][0]["run"].split("<<'EOF'\n", 1)[1].split("\nEOF")[0]
    env = {**os.environ, "MODE": mode, "NEEDS": json.dumps({k: {"result": v} for k, v in results.items()})}
    return subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True).returncode


def test_gate():
    ok = dict(plan="success", licences="success", secrets="success", settings="success")
    assert gate("report", **ok) == 0 and gate("block", **ok) == 0
    assert gate("report", **{**ok, "licences": "skipped"}) == 0                  # nothing changed: skipped jobs pass
    assert gate("report", **{**ok, "licences": "failure", "secrets": "failure"}) == 0   # report mode never blocks on a check
    assert gate("block", **{**ok, "licences": "failure"}) == 1
    assert gate("block", **{**ok, "secrets": "failure"}) == 1
    assert gate("block", **{**ok, "settings": "failure"}) == 1
    assert gate("report", plan="failure", licences="skipped", secrets="skipped", settings="skipped") == 1   # a broken plan always blocks
    assert gate("report", **{**ok, "licences": "cancelled"}) == 1


def test_gate_waits_for_every_job():
    assert set(WF["jobs"]["gate"]["needs"]) == set(WF["jobs"]) - {"gate"} and WF["jobs"]["gate"]["if"] == "always()"


def test_third_party_actions_are_pinned_to_a_commit():
    uses = [u for text in TEXT.values() for u in re.findall(r"^\s*(?:- )?uses:\s*(\S+)", text, re.M)]
    own = [u for u in uses if u.startswith("selfxyz/compliance/")]
    assert len(own) == 4 and all(u.endswith("@v1") for u in own), own   # 3 in the workflow, 1 in the template
    bad = [u for u in uses if u not in own and not u.startswith("./") and not re.search(r"@[0-9a-f]{40}$", u)]
    assert not bad, bad


def test_licences_script_gets_every_variable_it_reads():
    script = open(os.path.join(ROOT, "bin", "licences.sh")).read()
    reads = set(re.findall(r"\$\{?([A-Z_]{3,})", script)) - {"ROOT", "OFFLINE", "GITHUB_STEP_SUMMARY"}
    step = next(s for s in WF["jobs"]["licences"]["steps"] if s.get("name") == "Scan and apply policy")
    given = set(step["env"]) | set(WF["env"])
    assert reads <= given, reads - given


def test_plan_outputs_match_what_jobs_read():
    declared = set(WF["jobs"]["plan"]["outputs"])
    read = set(re.findall(r"needs\.plan\.outputs\.(\w+)", TEXT[".github/workflows/compliance.yml"]))
    plan_py = open(os.path.join(ROOT, "bin", "plan.py")).read()
    written = set(re.findall(r'(?:\\n|")(\w+)=\{', plan_py[plan_py.index('f.write(f"matrix='):plan_py.index("GITHUB_STEP_SUMMARY")]))
    assert read <= declared and declared - {"visibility", "default_branch"} == written, (read, declared, written)


def test_branch_rules():
    sys.path.insert(0, os.path.join(ROOT, "bin"))
    from settings import branch_problems as problems
    pr = lambda n: {"type": "pull_request", "parameters": {"required_approving_review_count": n}}
    ff = {"type": "non_fast_forward"}
    assert problems([pr(1), ff], 1) == [] and problems([pr(0), pr(2), ff], 2) == []   # several rulesets: the strictest counts
    assert len(problems([], 1)) == 2 and "pull request" in problems([ff], 1)[0]
    assert problems([pr(0), ff], 1) == ["pull requests need 0 approval(s), expected at least 1"]
    assert problems([pr(0), ff], 0) == [] and problems([pr(1)], 1) == ["force pushes are not blocked"]


def test_dependabot_alerts():
    sys.path.insert(0, os.path.join(ROOT, "bin"))
    from settings import dependabot
    assert dependabot(None) == (None, False)                                           # the list call worked: alerts are on
    problem, finding = dependabot('{"message":"Dependabot alerts are disabled for this repository.","status":"403"}')
    assert finding and "off" in problem
    problem, finding = dependabot("gh: Resource not accessible by integration (HTTP 403)")
    assert not finding and "could not read" in problem                                 # unreadable is a warning, not a finding


def test_settings_job_can_read_what_it_checks():
    perms = WF["jobs"]["settings"]["permissions"]
    caller = yaml.safe_load(TEXT["template/.github/workflows/compliance.yml"])["permissions"]
    assert perms.get("vulnerability-alerts") == "read" and all(caller.get(k) == v for k, v in perms.items()), (perms, caller)


def test_branch_rules_config():
    import jsonschema
    schema = json.load(open(os.path.join(ROOT, "schema", "compliance.schema.json")))
    ok = lambda rules: not list(jsonschema.Draft202012Validator(schema).iter_errors({"version": 1, "kind": "service", "branch_rules": rules}))
    assert ok({"approvals": 2, "protected": ["main", "staging"]}) and not ok({"approvals": 0})   # 0 approvals needs a reason
    assert ok({"approvals": 0, "reason": "Docs-only repo with a single maintainer."})


def test_no_file_over_200_lines():
    out = subprocess.run(["git", "-C", ROOT, "ls-files", "--cached", "--others", "--exclude-standard"], capture_output=True, text=True).stdout.split("\n")
    files = [f for f in out if f] or [os.path.relpath(os.path.join(d, f), ROOT) for d, _, fs in os.walk(ROOT) if ".git" not in d and "__pycache__" not in d for f in fs]
    long = {f: n for f in files if os.path.isfile(os.path.join(ROOT, f)) and (n := sum(1 for _ in open(os.path.join(ROOT, f), errors="replace"))) > 200}
    assert not long, long


if __name__ == "__main__":
    main(globals())
