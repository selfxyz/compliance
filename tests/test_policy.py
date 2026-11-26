"""policy/policy.py with policy.yml: the action per licence and profile, the LICENSE file check, exceptions.
Also scanner/licences.py, which sets the category the rules read."""
import csv, datetime, os, subprocess, sys

from helpers import ROOT, TMP, main, repo

sys.path[:0] = [os.path.join(ROOT, "policy"), os.path.join(ROOT, "scanner")]
import policy
from licences import classify

POLICY = policy.load_yaml(os.path.join(ROOT, "policy", "policy.yml"))
COLS = ["ecosystem", "name", "version", "licence", "licence_source", "scope", "direct", "category"]


def findings(licence, profile, scope="prod", name="x"):
    path = os.path.join(TMP, "deps.csv")
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows([COLS, ["npm", name, "1", licence, "", scope, "", classify(licence)]])
    return policy.dep_findings(path, POLICY["rules"], profile)


def action(licence, profile, scope="prod"):
    found = findings(licence, profile, scope)
    return (found[0]["action"], found[0]["rule"]) if found else ("pass", None)


def test_rules_per_profile():
    cases = [  # licence, profile -> action, rule
        ("AGPL-3.0-only", "backend", ("fail", "agpl")),
        ("GNU Affero General Public License v3", "backend", ("fail", "agpl")),
        ("GPL-3.0", "distributed", ("fail", "gpl")),
        ("GPL-3.0", "web", ("flag", "gpl")),
        ("GPL-3.0", "contracts", ("flag", "gpl")),
        ("GPL-3.0", "backend", ("pass", None)),
        ("GNU General Public License, version 2", "distributed", ("fail", "gpl")),   # how Maven POMs spell it
        ("GNU Lesser General Public License", "distributed", ("flag", "lgpl")),
        ("GNU Library General Public License", "distributed", ("flag", "lgpl")),
        ("LGPL-2.1-or-later", "distributed", ("flag", "lgpl")),
        ("LGPL-2.1-or-later", "backend", ("pass", None)),
        ("(BSD-3-Clause OR GPL-2.0)", "distributed", ("pass", None)),                # explicit OR with a permissive option
        ("MIT", "distributed", ("pass", None)),
        ("MPL-2.0", "distributed", ("pass", None)),
        ("SSPL-1.0", "backend", ("flag", "sspl")),
        ("BUSL-1.1", "backend", ("flag", "busl")),
        ("", "backend", ("flag", "no-licence")),
        ("CDLA-Permissive-2.0", "backend", ("flag", "non-standard")),
    ]
    bad = [(lic, prof, want, action(lic, prof)) for lic, prof, want in cases if action(lic, prof) != want]
    assert not bad, bad


def test_dev_only_passes():
    assert action("AGPL-3.0", "distributed", scope="dev") == ("pass", None)


def test_categories():
    want = {"MIT": "ok", "MIT OR Apache-2.0": "ok", "GPL-3.0": "strong", "LGPL-3.0": "weak", "MPL-2.0": "weak", "BUSL-1.1": "restricted",
            "": "unknown", "Custom Licence": "non-standard", "(MIT OR GPL-2.0)": "dual", "MIT / GPL-3.0": "strong"}
    got = {lic: classify(lic) for lic in want}
    assert got == want, got


def licence_file_action(names, visibility):
    d, _ = repo("lic-" + "-".join(names).lower() + visibility, {n: "x" for n in names})
    return [f["action"] for f in policy.file_findings(d, "HEAD", POLICY["files"], visibility) if f["rule"] == "licence_file"]


def test_licence_file():
    assert licence_file_action(["README.md"], "public") == ["fail"]
    assert licence_file_action(["README.md"], "private") == ["flag"]
    assert licence_file_action(["LICENSE"], "public") == []
    assert licence_file_action(["LICENSE-MIT", "LICENSE-APACHE"], "public") == []   # Rust dual-licence file names


def test_fonts_and_binaries_are_flagged():
    d, _ = repo("files", {"LICENSE": "x", "assets/Brand.otf": "x", "libs/vendor.jar": "x", "node_modules/a/b.ttf": "x"})
    got = sorted((f["rule"], f["name"]) for f in policy.file_findings(d, "HEAD", POLICY["files"], "private"))
    assert got == [("binaries", "libs/vendor.jar"), ("fonts", "assets/Brand.otf")], got


def test_exceptions_match_expire_and_scope_to_repos():
    today = datetime.date.today().isoformat()
    exc = [{"id": "other-repo", "repos": ["selfxyz/other"], "package": "gpl-*", "reason": "r"},
           {"id": "old", "package": "gpl-*", "reason": "r", "expires": "2020-01-01"},
           {"id": "ok", "repos": ["selfxyz/*"], "package": "gpl-*", "rule": "gpl", "reason": "r"}]
    f = policy.apply_exceptions(findings("GPL-3.0", "distributed", name="gpl-lib"), exc, "selfxyz/app", today)[0]
    assert f["excepted"] == "ok" and f["exception_expired"] == "old", f
    f = policy.apply_exceptions(findings("GPL-3.0", "distributed", name="gpl-lib"), exc[:2], "selfxyz/app", today)[0]
    assert "excepted" not in f


def test_missing_scan_result_is_an_error():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "policy", "policy.py"), "--repo", "selfxyz/x", "--profile", "backend", "--head", "/nonexistent.csv"], capture_output=True, text=True)
    assert r.returncode != 0 and "no scan result" in r.stderr, r.stderr


if __name__ == "__main__":
    main(globals())
