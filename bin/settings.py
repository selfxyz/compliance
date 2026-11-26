#!/usr/bin/env python3
"""Check the repo's own GitHub settings: Dependabot alerts are on, and protected branches are protected.

Dependabot alerts: listing them works only when they are enabled.
Branch rules: each protected branch needs a pull request, enough approvals, and a force-push block.
Read from rulesets; classic branch protection cannot be read with the workflow token.

Env: REPO, DEFAULT_BRANCH, MODE, RULES (json from plan.py: {"protected": [...], "approvals": n})
Exit: 1 only in block mode, when a setting is wrong. A setting that cannot be read is a warning.
"""
import json, os, subprocess, sys

DEFAULT_APPROVALS = 1


def gh(path):
    """(data, None) on success, (None, error text) on failure."""
    r = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    return (json.loads(r.stdout), None) if r.returncode == 0 else (None, r.stdout + r.stderr)


def dependabot(error):
    """(problem, is it a finding) from the error of listing Dependabot alerts; None means they are on."""
    if error is None:
        return None, False
    if "disabled" in error.lower():
        return "Dependabot alerts are off. Turn them on in Settings, Advanced Security.", True
    return f"could not read Dependabot alerts ({error.strip().splitlines()[-1][:120]})", False


def branch_problems(rules, approvals):
    """What is missing on a branch, given the active rules GitHub returns for it."""
    by_type = {}
    for r in rules:
        by_type.setdefault(r["type"], []).append(r.get("parameters") or {})
    out = []
    if "pull_request" not in by_type:
        out.append("no rule requires a pull request, so anyone with write access can push to it")
    else:
        have = max(p.get("required_approving_review_count", 0) for p in by_type["pull_request"])
        if have < approvals:
            out.append(f"pull requests need {have} approval(s), expected at least {approvals}")
    if "non_fast_forward" not in by_type:
        out.append("force pushes are not blocked")
    return out


def main():
    repo, mode = os.environ["REPO"], os.environ.get("MODE", "report")
    want = json.loads(os.environ.get("RULES") or "{}")
    approvals = want.get("approvals", DEFAULT_APPROVALS)
    rows = []  # (what, problem, is it a finding)

    problem, finding = dependabot(gh(f"repos/{repo}/dependabot/alerts?per_page=1")[1])
    rows.append(("Dependabot alerts", problem, finding))
    for branch in want.get("protected") or [os.environ["DEFAULT_BRANCH"]]:
        rules, error = gh(f"repos/{repo}/rules/branches/{branch}")
        if error:
            rows.append((f"Branch `{branch}`", "could not read its rules (API error)", False))
        elif not rules and (gh(f"repos/{repo}/branches/{branch}")[0] or {}).get("protected"):
            rows.append((f"Branch `{branch}`", "classic branch protection is on, but its settings are not readable here. Move it to a ruleset.", False))
        else:
            found = branch_problems(rules, approvals)
            rows += [(f"Branch `{branch}`", p, True) for p in found] or [(f"Branch `{branch}`", None, False)]

    failed = any(finding for _, _, finding in rows)
    L = ["## Repo settings", "", f"Expected: Dependabot alerts on. Protected branches: pull request, {approvals} approval(s), no force push.", "",
         "| Setting | Result |", "|---|---|"]
    for what, problem, finding in rows:
        if problem:
            print(f"::{'error' if finding and mode == 'block' else 'warning'} title=Repo settings::{what.replace('`', '')}: {problem}")
        L.append(f"| {what} | {problem or 'ok'} |")
    if want.get("reason"):
        L += ["", f"Repo-level setting: {approvals} approval(s). Reason: {want['reason']}"]
    L += ["", "Rulesets can let some people bypass them. That list is not visible to this check."]
    print("\n".join(L))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("\n".join(L) + "\n")
    sys.exit(1 if failed and mode == "block" else 0)


if __name__ == "__main__":
    main()
