"""Licence lookups (deps.dev, npm, Maven, CocoaPods, GitHub) with an on-disk cache."""
import hashlib, json, os, re, subprocess, urllib.parse, urllib.request


UA = {"User-Agent": "oss-licence-scan"}
CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "oss-scan", "licences.json")


class Lookup:
    def __init__(self, offline):
        self.offline = offline
        self.c = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
        self.calls = 0
        self.failed = 0

    def save(self):
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        json.dump(self.c, open(CACHE, "w"))

    def _get(self, url, raw=False):
        self.calls += 1
        try:
            b = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read()
            return b.decode("utf-8", "replace") if raw else json.loads(b)
        except Exception:
            return None

    def depsdev(self, system, items):
        want = [(n, v) for n, v in items if f"{system}:{n}@{v}" not in self.c]
        if self.offline:
            return
        for i in range(0, len(want), 400):
            body = {"requests": [{"versionKey": {"system": system, "name": n, "version": v}} for n, v in want[i:i + 400]]}
            tok = None
            while True:
                if tok:
                    body["pageToken"] = tok
                req = urllib.request.Request("https://api.deps.dev/v3alpha/versionbatch", data=json.dumps(body).encode(),
                                             headers={**UA, "Content-Type": "application/json"})
                self.calls += 1
                res = json.load(urllib.request.urlopen(req, timeout=60))
                for x in res.get("responses", []):
                    vk, ver = x["request"]["versionKey"], x.get("version")
                    lic = [l for l in (ver or {}).get("licenses", []) if l != "non-standard"]
                    self.c[f'{system}:{vk["name"]}@{vk["version"]}'] = {"lic": " / ".join(lic), "src": ("deps.dev" + (" (lists several licences; AND/OR not stated)" if len(lic) > 1 else "")) if lic else ""}
                tok = res.get("nextPageToken")
                if not tok:
                    break
            self.save()

    def npm(self, items):
        self.depsdev("NPM", items)
        for n, v in items:
            e = self.c.get(f"NPM:{n}@{v}")
            if e is None or e["lic"] or e.get("tried") or self.offline:
                continue
            r = self._get("https://registry.npmjs.org/" + urllib.parse.quote(n, safe="@") + "/" + v)
            if r is None:
                self.failed += 1
                continue
            lic = r.get("license")
            if isinstance(lic, dict):
                lic = lic.get("type")
            if not lic and r.get("licenses"):
                lic = " OR ".join(x.get("type", "") for x in r["licenses"] if isinstance(x, dict))
            self.c[f"NPM:{n}@{v}"] = {"lic": lic or "", "src": "npm registry" if lic else "", "tried": 1}
        self.save()
        return {(n, v): self.c.get(f"NPM:{n}@{v}", {"lic": "", "src": ""}) for n, v in items}

    def maven(self, items):
        self.depsdev("MAVEN", items)
        for n, v in items:
            e = self.c.get(f"MAVEN:{n}@{v}")
            if e is None or e["lic"] or e.get("tried") or self.offline:
                continue
            g, a = n.split(":")
            lic, src = "", ""
            for base, label in (("https://repo1.maven.org/maven2", "Maven Central POM"), ("https://dl.google.com/android/maven2", "Google Maven POM"),
                                ("https://jitpack.io", "JitPack POM")):
                pom = self._get(f"{base}/{g.replace('.', '/')}/{a}/{v}/{a}-{v}.pom", raw=True)
                if not pom:
                    continue
                m = re.search(r"<licenses>(.*?)</licenses>", pom, re.S)
                names = re.findall(r"<name>\s*(.*?)\s*</name>", m.group(1), re.S) if m else []
                lic, src = " / ".join(names), label
                break
            if not src:
                self.failed += 1  # no POM reachable: stay unknown this run, try again next run
                continue
            self.c[f"MAVEN:{n}@{v}"] = {"lic": lic, "src": src if lic else src + " (no licence in POM)", "tried": 1}
        self.save()
        return {(n, v): self.c.get(f"MAVEN:{n}@{v}", {"lic": "", "src": ""}) for n, v in items}

    def cargo(self, items):
        self.depsdev("CARGO", items)
        return {(n, v): self.c.get(f"CARGO:{n}@{v}", {"lic": "", "src": ""}) for n, v in items}

    def github(self, slugs):
        """Licence of a public GitHub repository, as GitHub detects it (used for Swift packages)."""
        out = {}
        for slug in slugs:
            k = f"GH:{slug}"
            if k not in self.c and not self.offline:
                r = None
                try:
                    g = subprocess.run(["gh", "api", f"repos/{slug}/license"], capture_output=True, timeout=30)
                    self.calls += 1
                    r = json.loads(g.stdout) if g.returncode == 0 else None
                except Exception:
                    r = None
                if r is None:
                    r = self._get(f"https://api.github.com/repos/{slug}/license")
                if r is None:
                    self.failed += 1
                    out[slug] = {"lic": "", "src": ""}
                    continue
                spdx_id = (r.get("license") or {}).get("spdx_id") or ""
                self.c[k] = {"lic": "" if spdx_id in ("", "NOASSERTION") else spdx_id, "src": "GitHub licence detection for the source repository"}
            out[slug] = self.c.get(k, {"lic": "", "src": ""})
        self.save()
        return out

    def pods(self, items):
        out = {}
        for n, v in items:
            k = f"POD:{n}@{v}"
            if k not in self.c and not self.offline:
                h = hashlib.md5(n.encode()).hexdigest()
                r = self._get(f"https://cdn.cocoapods.org/Specs/{h[0]}/{h[1]}/{h[2]}/{n}/{v}/{n}.podspec.json")
                if r is None:
                    self.failed += 1
                    out[(n, v)] = {"lic": "", "src": ""}
                    continue
                lic = r.get("license")
                if isinstance(lic, dict):
                    lic = lic.get("type")
                self.c[k] = {"lic": lic or "", "src": "CocoaPods trunk podspec" if lic else ""}
            out[(n, v)] = self.c.get(k, {"lic": "", "src": ""})
        self.save()
        return out
