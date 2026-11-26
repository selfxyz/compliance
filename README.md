# selfxyz/compliance

A reusable GitHub Actions workflow that checks a repo's dependency licences, secrets, Dependabot alerts and branch rules.

## Use it

1. Copy `template/.github/workflows/compliance.yml` into the repo, unchanged.
2. Open a PR. The job summary shows what was found and which rules apply.

With no config, folders and kinds are detected from the repo's files, and findings are reported without blocking.

## When it runs

| Event | What runs |
|---|---|
| A PR is opened, or gets new commits | Checks for that PR. Licences only for workspaces whose dependency files changed. |
| A push to the default branch (a merged PR) | The same, for the merged commits. |
| Every Monday, 05:17 UTC | Everything: all workspaces, whole git history for secrets. |
| "Run workflow" button | Everything. |

## What it does

| Job | Does |
|---|---|
| `plan` | Reads `.github/compliance.json`, or detects the workspaces, and picks the ones to scan. |
| `licences` | One job per workspace: reads the lockfiles, looks up each licence, applies the rules. |
| `secrets` | gitleaks on the new commits. |
| `settings` | Dependabot alerts are on. The default branch needs a pull request, 1 approval, and no force push. |
| `gate` | The check to require: `compliance / gate`. |

Lockfiles read: `pnpm-lock.yaml` (repo root), `package-lock.json`, `yarn.lock`, `bun.lock`, `Cargo.lock`, `Podfile.lock`, podspecs, Swift packages, Gradle build files, Solidity SPDX headers, Circom includes, git submodules.

## What fails the gate

| Problem | Report mode (default) | Block mode |
|---|---|---|
| Invalid config, wrong kind, or an uncovered dependency manifest | fails | fails |
| A new `fail` licence finding | warning | fails |
| A leaked secret | warning | fails |
| Dependabot alerts are off | warning | fails |
| A branch without the required rules | warning | fails |
| A `flag` finding | listed | listed |

On PRs and pushes, a finding that was already there is listed as existing and does not fail the gate.

## Kinds and rules

A repo says what a folder **is**. `policy/kinds.yml` and `policy/policy.yml` decide the rules.

| Rules | Kinds | GPL | LGPL |
|---|---|---|---|
| `distributed` | `ios-app`, `android-app`, `native-sdk`, `rn-module`, `npm-package` | fail | flag |
| `web` | `web-app`, `docs` | flag | pass |
| `contracts` | `contracts` | flag | pass |
| `backend` | `service`, `circuits`, `infra` | pass | pass |
| `excluded` (not scanned) | `tooling`, `example`, `test-app` | | |

AGPL fails everywhere. Several kinds on one folder: the strictest rules win. The other flags (SSPL, BUSL, no licence, fonts, vendored binaries, missing LICENSE) are in `policy/policy.yml`.

## compliance.json

Optional. Add `.github/compliance.json` to correct a detected kind, leave a folder out, block on findings, or change a rule for this repo. Start from `template/` or `examples/`.

| Field | Example | Effect |
|---|---|---|
| `version` | `1` | Required. |
| `kind` | `"service"` | The whole repo is one thing. |
| `workspaces` | `[{ "path": "apps/web", "kind": "web-app" }]` | One entry per folder. Use instead of `kind`. |
| `mode` | `"block"` | Findings fail the gate. Default `report`. |
| `branch_rules` | `{ "protected": ["main", "staging"], "approvals": 2 }` | Branches to check and approvals needed. `0` needs a `reason`. |
| `override` (in a workspace) | `{ "profile": "backend", "reason": "..." }` | Other rules for that folder, or `excluded`. |
| `ignore` | `["**/fixtures/**"]` | Paths left out of every check. |
| `exceptions` | `[{ "package": "snarkjs", "rule": "gpl", "reason": "...", "expires": "2027-06-01" }]` | Accept one finding. |
| `curations` | `[{ "name": "ring", "licence": "ISC AND MIT AND OpenSSL", "evidence": "LICENSE file in the crate" }]` | Set a licence the scanner got wrong or could not find. Also `ships` and `note`. |

Reasons are required, 20 characters or more. The job summary lists every override, ignore, exception and curation. `examples/monorepo.json` uses all of them.

## Troubleshooting

| Message | Fix |
|---|---|
| `... is declared X but <file> shows it is Y` | Change the folder's `kind` to Y, give that folder its own workspace, or add an `override` with a reason. |
| `not covered by any workspace: <file>` | Add a workspace for that folder (`tooling`, `example` or `test-app` if it never ships), or list it in `ignore`. |
| `workspace path ... does not exist at this commit` | Fix the `path`; it is relative to the repo root. |
| A schema error such as `Additional properties are not allowed` | Compare with `schema/compliance.schema.json`. |
| "Not covered by the scanner" in the summary | That lockfile type is not read (Python, Go, `bun.lockb`, a Rust crate without `Cargo.lock`). Commit a supported lockfile if there is one. |
| A wrong or missing licence on a package | Add a `curations` entry with the correct licence and the evidence. |
| `Possible secret committed` | Rotate the secret first. Then remove it, or allowlist a false positive in `.gitleaks.toml`. |
| `Dependabot alerts are off` | Turn them on: Settings, Advanced Security, Dependabot alerts. |
| `could not read Dependabot alerts` | The caller workflow is missing `vulnerability-alerts: read`. Copy the template again. |
| `classic branch protection is on, but its settings are not readable` | Move the rule to a ruleset. |
| The gate is red but every check passed | A job was cancelled. Re-run it. |

To see the plan for a repo without pushing: `python3 bin/plan.py --repo <path> --full`.

## Set up a repo with Claude

Open the repo in Claude Code and paste:

```text
Set up the compliance workflow from selfxyz/compliance in this repo.

1. Read selfxyz/compliance: README.md, template/, examples/, policy/kinds.yml, schema/compliance.schema.json.
2. Copy template/.github/workflows/compliance.yml to .github/workflows/compliance.yml, unchanged.
3. Look at this repo's folders, lockfiles and deploy or publish setup. Decide what each folder is, using
   only the kinds in policy/kinds.yml. Published to npm or shipped in an app = strict kinds; runs only
   on our servers = service; never ships = tooling, example or test-app.
4. Write .github/compliance.json only if it is needed: detection would get a kind wrong, a folder must
   not be scanned, or the repo needs its own rule. Keep it small. Leave "mode" out unless I ask for block.
5. Check it: clone selfxyz/compliance next to this repo and run
   python3 <compliance>/bin/plan.py --repo . --full
   Fix every error. Show me the plan: each workspace, its kind, its rules, and what the scanner cannot read.
6. Do not add overrides, ignores or exceptions to make an error go away. If a finding looks wrong,
   stop and tell me what you found.
7. Do not commit or push. Tell me which files you added.
```

## Tests

```
pip install pyyaml==6.0.2 jsonschema==4.23.0
for t in tests/test_*.py; do python3 "$t" || break; done
```

## Licence

Business Source License 1.1 ([LICENSE.md](LICENSE.md)).
