"""Licence strings: SPDX ids, common aliases, and the category the policy rules use."""
import re


SPDX_OK = set("""MIT MIT-0 ISC Apache-2.0 BSD-2-Clause BSD-3-Clause 0BSD Unlicense CC0-1.0 Zlib
BlueOak-1.0.0 Python-2.0 CC-BY-4.0 CC-BY-3.0 BSL-1.0 WTFPL Unicode-3.0 Unicode-DFS-2016 OFL-1.1
MPL-2.0 MPL-1.1 EPL-1.0 EPL-2.0 CDDL-1.0 CDDL-1.1 LGPL-2.0-only LGPL-2.0-or-later LGPL-2.1
LGPL-2.1-only LGPL-2.1-or-later LGPL-3.0 LGPL-3.0-only LGPL-3.0-or-later GPL-2.0 GPL-2.0-only
GPL-2.0-or-later GPL-3.0 GPL-3.0-only GPL-3.0-or-later AGPL-3.0 AGPL-3.0-only AGPL-3.0-or-later
SSPL-1.0 BUSL-1.1 OpenSSL Artistic-2.0 BSD-4-Clause libpng-2.0 IJG X11 curl NCSA PSF-2.0
Classpath-exception-2.0""".split())

# Common spellings in POMs and podspecs -> SPDX id. Anything else stays as written.
ALIAS = {re.sub(r"[^a-z0-9]", "", k.lower()): v for k, v in {
    "The Apache Software License, Version 2.0": "Apache-2.0", "Apache License, Version 2.0": "Apache-2.0", "Apache License 2.0": "Apache-2.0",
    "The Apache License, Version 2.0": "Apache-2.0", "Apache 2.0": "Apache-2.0", "Apache-2": "Apache-2.0", "Apache": "Apache-2.0", "Apache 2": "Apache-2.0",
    "The MIT License": "MIT", "MIT License": "MIT", "The MIT License (MIT)": "MIT", "mit": "MIT", "zlib": "Zlib", "CC0": "CC0-1.0",
    "Eclipse Public License 1.0": "EPL-1.0", "Eclipse Public License - v 1.0": "EPL-1.0", "ISC License": "ISC",
    "The BSD 3-Clause License": "BSD-3-Clause", "BSD 3-Clause": "BSD-3-Clause", "BSD 2-Clause": "BSD-2-Clause",
}.items()}


def norm(lic):
    return " / ".join(ALIAS.get(re.sub(r"[^a-z0-9]", "", p.lower()), p) for p in lic.split(" / ")) if lic else lic


STRONG = re.compile(r"\bA?GPL|SSPL", re.I)
WEAK = re.compile(r"LGPL|MPL|EPL|CDDL|EUPL|OSL-|CC-BY-SA|Lesser", re.I)
RESTRICTED = re.compile(r"BUSL|Business Source|Commons Clause|FSL-|Elastic|SEE LICEN|proprietary|Community License|Terms of Service|Development Kit License|^Copyright$", re.I)


def _one(p):
    if re.search(r"LGPL|Lesser", p, re.I) or (WEAK.search(p) and not STRONG.search(p)):
        return "weak"
    if STRONG.search(p):
        return "strong"
    if RESTRICTED.search(p):
        return "restricted"
    if all(t in SPDX_OK for t in re.split(r"\s+AND\s+|\s+WITH\s+", p)):
        return "ok"
    return "non-standard"


def classify(lic):
    """Categories: ok, strong, weak, restricted, unknown, non-standard, dual.
    "dual" needs an explicit OR. A list joined with " / " (relation not stated) takes its worst member."""
    if not lic:
        return "unknown"
    worst = lambda cs: next((c for c in ("strong", "weak", "restricted", "non-standard") if c in cs), "ok")
    listed = []
    for item in lic.split(" / "):
        alts = [_one(a.strip(" ()")) for a in re.split(r"\s+OR\s+", item)]
        listed.append("dual" if len(alts) > 1 and "ok" in alts and worst(alts) != "ok" else worst(alts))
    if all(c in ("ok", "dual") for c in listed):
        return "dual" if "dual" in listed else "ok"
    return worst(listed)
