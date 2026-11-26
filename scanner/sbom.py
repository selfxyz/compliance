"""SPDX 2.3 writer."""
import datetime, json, os, re, urllib.parse

from licences import SPDX_OK


def spdx(rows, a, out):
    """Write <out>/sbom.spdx.json (SPDX 2.3) with one package per dependency row."""
    part = "all" if a.path in ("", ".") else a.path
    slug = re.sub(r"[^A-Za-z0-9.]+", "-", part)
    extracted, pkgs, rels = {}, [], []
    doc = "SPDXRef-DOCUMENT"
    for i, r in enumerate(rows):
        lic = r["licence"]
        if not lic:
            decl = "NOASSERTION"
        elif all(t in SPDX_OK for t in re.split(r"\s+(?:AND|OR|WITH)\s+", lic.strip("()"))):
            decl = lic
        else:
            ref = "LicenseRef-scan-" + re.sub(r"[^A-Za-z0-9.]+", "-", lic).strip("-")[:60]
            extracted[ref] = lic
            decl = ref
        sid = f"SPDXRef-Package-{i}"
        ptype = {"npm": "npm", "maven": "maven", "cocoapods": "cocoapods", "cargo": "cargo", "swiftpm": "swift"}.get(r["eco"])
        if r["eco"] == "cocoapods" and not re.match(r"^[\d.]+", r["version"]):
            ptype = None  # a version requirement, not a version
        if r["eco"] == "maven" and (r["name"].startswith("catalog:") or r["version"] == "unresolved" or "$" in r["version"]):
            ptype = None
        pk = dict(SPDXID=sid, name=r["name"], versionInfo=r["version"] or "NOASSERTION", downloadLocation="NOASSERTION",
                  filesAnalyzed=False, licenseConcluded="NOASSERTION", licenseDeclared=decl, copyrightText="NOASSERTION",
                  comment=f"scope={r['scope']}; licence source: {r['source'] or 'none'}")
        if ptype and not r["version"].startswith(("http", "git")):
            nm = r["name"].replace(":", "/") if ptype == "maven" else ("github.com/" + r["name"] if ptype == "swift" else urllib.parse.quote(r["name"], safe="/"))
            pk["externalRefs"] = [dict(referenceCategory="PACKAGE-MANAGER", referenceType="purl", referenceLocator=f"pkg:{ptype}/{nm}@{r['version']}")]
        pkgs.append(pk)
        rels.append(dict(spdxElementId="SPDXRef-Root", relationshipType="DEV_DEPENDENCY_OF" if r["scope"] == "dev" else "DEPENDS_ON", relatedSpdxElement=sid))
        if r["scope"] == "dev":
            rels[-1] = dict(spdxElementId=sid, relationshipType="DEV_DEPENDENCY_OF", relatedSpdxElement="SPDXRef-Root")
    root = dict(SPDXID="SPDXRef-Root", name=a.repo_name if part == "all" else f"{a.repo_name}/{part}", versionInfo="NOASSERTION", downloadLocation="NOASSERTION", filesAnalyzed=False,
                licenseConcluded="NOASSERTION", licenseDeclared="NOASSERTION", copyrightText="NOASSERTION", comment=f"git commit {a.sha}")
    d = dict(spdxVersion="SPDX-2.3", dataLicense="CC0-1.0", SPDXID=doc, name=f"{a.repo_name}-{slug}-{a.sha[:9]}",
             documentNamespace=f"https://spdx.org/spdxdocs/{a.repo_name}-{slug}-{a.sha}",
             creationInfo=dict(created=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), creators=["Tool: oss-scan-draft"]),
             packages=[root] + pkgs, relationships=[dict(spdxElementId=doc, relationshipType="DESCRIBES", relatedSpdxElement="SPDXRef-Root")] + rels,
             hasExtractedLicensingInfos=[dict(licenseId=k, extractedText=v, name=v) for k, v in sorted(extracted.items())])
    json.dump(d, open(os.path.join(out, "sbom.spdx.json"), "w"), separators=(",", ":"))
