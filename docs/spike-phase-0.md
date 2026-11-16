# Spike — Phase 0: can a pipeline deploy a Fabric App?

Goal: prove, by hand and before writing any pipeline, that a service principal
can deploy and redeploy one specific Fabric App (`app-dre-sankey`, the
write-back version) with the Rayfin CLI, from a machine that has no deployment
history. Everything the pipeline will do later is exercised here once.

Scope: the app only. The semantic model it reads already exists and is deployed
elsewhere; the app is bound to it by IDs.

## What the source code already answers

Read from `microsoft/rayfin` (commit `0979439`, 2026-10-13) and
`@microsoft/fabric-app-data-cli` 3.0.0. These are expectations to confirm, not
results.

| # | Question | Answer from the code | Consequence for the pipeline |
|---|---|---|---|
| A | A CI run has no `rayfin/.deployments.json`. Does `rayfin up` create a duplicate item? | No. It looks up the item by display name. Non-interactive without `--yes`, it **fails** ("already exists ... pass `--yes`"); with `--yes`, it reuses it. | Always pass `--item-name` and `--yes`. Without `--yes` the run fails, it never duplicates. |
| B | Does `--yes` approve anything else? | Also a capacity assignment, but only when the target workspace has no capacity. An existing assignment is never replaced. | Target an existing workspace on capacity (`--workspace-id`); `--yes` then only means "reuse the item". |
| C | Service principal sign-in | `rayfin login --service-principal --client-id --client-secret --tenant`; the secret is persisted under `~/.rayfin/` (file mode 0600) and reused by later commands. | One login step per job. |
| D | Linux runner | The token cache needs an OS keychain (libsecret). In a container, or without the keychain, it fails unless `RAYFIN_ENCRYPTION_FALLBACK_ENABLED=true`. | Set it on hosted runners (ephemeral disk). |
| E | Secretless option | `RAYFIN_TOKEN` takes a bearer token and bypasses MSAL. The same token is returned for every scope, and the default scope is `https://api.fabric.microsoft.com/.default`. Steps that need a Power BI audience token (connectors, the semantic-model access probe) would get the Fabric one. | OIDC (`azure/login` + `az account get-access-token`) is possible for an app without Rayfin connectors. To be confirmed in T6b. |
| F | Does `fabric-app-data generate` need network or credentials? | No. It reads `fabric.yaml` and writes the TS file. `add --workspace <id> --item <id> -p <profile>` is offline too. | The pipeline writes a `prod` profile from variables and builds; no lookup at build time. |
| G | `allowedRedirectUris` in CI | After uploading static content, `up` adds the hosting origin to the list and POSTs the settings again (a workaround the code says is temporary). The settings sync earlier in the same run posts `rayfin.yml` without it. | Each full `up` restores it. Short window during deploy where the origin is missing (T4). Mitigation: keep the prod origin in `rayfin.yml` through `${VAR}` interpolation. |
| H | Destructive schema change | `up` stops with "Destructive schema changes detected" unless `--force`. `--dry-run` does not apply or diff schemas. | The deploy itself is the gate. A destructive change needs an explicit, approved `--force` run. |

## Before you start

- [ ] **Node 20/22/24 LTS on this machine.** No `node`/`npx` is on the PATH in
      the current shell; install with nvm-windows (`nvm install 24`).
- [ ] A **spike workspace** on a Fabric capacity, in a region with Fabric Apps,
      e.g. `fabric-apps-cicd-spike`. Empty. Disposable.
- [ ] A **service principal** (the one of `pyfabricops-template` works) with
      **Contributor** on the spike workspace. Admin only if T2 fails on Contributor.
- [ ] Tenant settings: *Fabric Apps (preview)* enabled, and *Service principals
      can use Fabric APIs* enabled for the SP's security group.
- [ ] Credentials in a local `.env` outside any repository (never typed in the
      terminal, never pasted in a chat):

      FAB_TENANT_ID=...
      FAB_CLIENT_ID=...
      FAB_CLIENT_SECRET=...
      SPIKE_WORKSPACE_ID=...
      MODEL_WORKSPACE_ID=...   # workspace of the existing dre-sankey
      MODEL_ITEM_ID=...        # dre-sankey semantic model

- [ ] A **separate copy of the app**. Never run the spike from the real
      `app-dre-sankey` folder: `rayfin up` rewrites its `.deployments.json`,
      `rayfin/.env` and `rayfin.yml`.

```powershell
git clone --branch feat/simulacao-write-back --depth 1 `
  https://github.com/alisonpezzott/fabric-app-dre-sankey-write-back.git `
  $env:TEMP\spike-app
Set-Location $env:TEMP\spike-app\app-dre-sankey
npm ci

# load the .env into this session only
Get-Content <path-to>\spike.env | ForEach-Object {
  # value stops at whitespace or '#', so inline comments never leak into it
  if ($_ -match '^\s*([A-Z_]+)\s*=\s*([^#\s]+)') { Set-Item "env:$($Matches[1])" $Matches[2].Trim('"',"'") }
}
```

### Setup notes (2026-11-16)

- Spike copy at `%TEMP%\spike-app`, branch `feat/simulacao-write-back`
  (`7ff68d2`), `npm ci` on Node 24.22.3 / npm 11.19.1: Rayfin CLI 1.36.2,
  fabric-app-data 3.0.0, esbuild 0.28.1 all run.
- npm 11.19 **skips install scripts** not approved in `allowScripts` (7
  packages: keytar, @azure/msal-node-extensions, @azure/msal-node-runtime,
  @swc/core, esbuild, protobufjs, tldjs). The CLIs run without them; T1 tells
  whether the token cache does. The pipeline's `npm ci` behaves the same.
- `vitest run` before binding: 7 suites fail on the missing
  `src/fabric.generated.ts` (gitignored, created by `fabric-app-data
  generate`), plus the known template failure in `validate-visual.spec.mjs`.
  **The pipeline must generate the profile before `npm test`**, not only
  before the build.

## Tests

Record each result in the table at the end. Keep the `--output json` files of
each run (`> t<n>.json`) as evidence, but check them for IDs before sharing.

### T1 — Service principal sign-in (Windows)

```powershell
npx rayfin login --service-principal `
  --client-id $env:FAB_CLIENT_ID --client-secret $env:FAB_CLIENT_SECRET --tenant $env:FAB_TENANT_ID
npx rayfin login status
```

Pass: "Signed in as service principal".

### T2 — First deploy by the SP into an empty workspace

```powershell
# bind the app to the existing model, in a profile named spike
npx fabric-app-data init
npx fabric-app-data add semanticModel modelo_dre -w $env:MODEL_WORKSPACE_ID -i $env:MODEL_ITEM_ID -p spike
npx fabric-app-data use spike -o src/fabric.generated.ts

npx rayfin up --workspace-id $env:SPIKE_WORKSPACE_ID --item-name app-dre-sankey --output json > t2.json
npx rayfin up status --json
```

Pass: one AppBackend `app-dre-sankey` and its SQL database in the workspace,
`up status` exits 0. Note in the portal who is the **owner** of each item (expected: the SP).

### T3 — Redeploy with no local history (the CI case)

```powershell
Remove-Item rayfin\.deployments.json, rayfin\.env, .env.local -ErrorAction SilentlyContinue
git checkout -- rayfin/rayfin.yml

# a) without --yes: must FAIL, not duplicate
npx rayfin up --workspace-id $env:SPIKE_WORKSPACE_ID --item-name app-dre-sankey --output json > t3a.json
# b) with --yes: must REUSE the same item ID as T2
npx rayfin up --workspace-id $env:SPIKE_WORKSPACE_ID --item-name app-dre-sankey --yes --output json > t3b.json
```

Pass: (a) exits non-zero with "already exists"; (b) same `fabricItemId` as
T2, still one item in the workspace, the SQL data written in T8 (if run before) is intact.

### T4 — Sign-in after a CI-style deploy

Right after T3b, open the app **in the Fabric portal** as a user (not the SP).

Pass: sign-in works, DRE numbers load (2025: net income R$ 13,088,163.15).
Also: `git diff rayfin/rayfin.yml` shows the hosting origin appended.
Optional: reload the app repeatedly during another T3b run to see whether the
redirect window of item G is noticeable.

### T5 — Schema changes

1. Add a nullable column to one entity in `rayfin/data/`, run the T3b command.
   Pass: applied without `--force`.
2. Remove that column, run again.
   Pass: run fails with "Destructive schema changes detected" and a non-zero exit code.
3. Run again with `--force`. Pass: applied.

### T6 — Linux, as on a hosted runner

Push the spike copy to a **private scratch repository** with a
`workflow_dispatch` workflow on `ubuntu-latest`: `actions/setup-node` (24),
`npm ci`, write the profile (T2), then:

```yaml
env:
  RAYFIN_ENCRYPTION_FALLBACK_ENABLED: "true"
run: |
  npx rayfin login --service-principal --client-id "$FAB_CLIENT_ID" \
    --client-secret "$FAB_CLIENT_SECRET" --tenant "$FAB_TENANT_ID"
  npx rayfin up --workspace-id "$SPIKE_WORKSPACE_ID" --item-name app-dre-sankey --yes --output json
  npx rayfin up status --json
```

Secrets in a GitHub environment, IDs as environment variables.
Pass: same item reused, exit 0. Then run it once **without**
`RAYFIN_ENCRYPTION_FALLBACK_ENABLED` to confirm item D (expected: keychain error).

**T6b (optional, secretless):** federated credential on the SP for the scratch
repository, `azure/login` with OIDC, then
`RAYFIN_TOKEN=$(az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv)`
and `RAYFIN_WORKSPACE_ID`, no `rayfin login`. Pass: the same deploy succeeds.

### T7 — The bundle points at the profile's model

Point the `spike` profile at a different model (any other semantic model the
user can read), redeploy, open the app. Pass: queries go to the new model
(errors are fine if the schema differs; what matters is which model is called).
Restore the profile afterwards.

### T8 — Users on an SP-owned app

As a user with a workspace role: create and save a simulation (write-back),
reload, confirm it is listed. Pass: `claims.sub` policies work the same as on
the user-deployed dev app.

### T9 — Cleanup

```powershell
npx rayfin logout
Remove-Item -Recurse -Force $env:TEMP\spike-app
```

Delete the spike workspace items (or the workspace) in the portal.

## Results

| Test | Result | Notes |
|---|---|---|
| T1 SP sign-in | Pass (2026-11-16) | Windows, keychain (DPAPI) worked with npm install scripts skipped. `login status`: identity `service_principal`, endpoint `api.fabric.microsoft.com/v1`, token ~2 h. MSAL warns "No client info in response" (expected for client credentials). |
| T2 first deploy, owner | Pass (2026-11-16), owner = SP (checked in portal) | 54 s, exit 0, no warnings. Target was the write-back workspace itself (the user-made app had been deleted first): one AppBackend + SQLDatabase + SQLEndpoint `app-dre-sankey`. `up status`: HTTP 200. The deployed bundle carried a bad model workspace ID (an inline `.env` comment leaked into the value); binding fixed locally, the T3b redeploy ships it. Lesson for the pipeline: validate IDs as GUIDs before `fabric-app-data add`. |
| T3a no `--yes` fails | Pass (2026-11-16) | exit 1, JSON `{"status":"error","code":"up-failed"}`: "already exists ... pass `--yes` to auto-accept". Nothing created, no local state written. |
| T3b `--yes` reuses item | Pass (2026-11-16) | 24 s, exit 0, same item ID as T2, same hosting URL. Still 1 AppBackend and 1 SQLDatabase. Hosting origin re-added to `allowedRedirectUris` (`configUpdated: true`). `up status` HTTP 200. Shipped the fixed model binding. |
| T4 sign-in after CI deploy | Pass (2026-11-16) | User opened the SP-owned app in the portal after T3b: sign-in OK, DRE loads, 2025 net income R$ 13,088,163.15 matches. |
| T5 schema add / drop / force | Pass (2026-11-16) | Full `up --yes` each time, on the real write-back DB. Add nullable `simulacao.spike_cicd`: exit 0, 22 s. Drop without `--force`: exit 1 in 9 s, DAB 400 "Drop column 'spike_cicd' from table 'simulacaos' ... would result in data loss but force mode is not enabled". Drop with `--force`: exit 0, 19 s; schema back to the original, `up status` 200. Note: `up` posts runtime settings before the schema step, so a refused schema leaves new settings with the old bundle; the pipeline should treat that run as failed and fix forward. |
| T6 Linux + fallback | | |
| T6b OIDC token | | |
| T7 profile binding | | |
| T8 users on SP-owned app | Pass (2026-11-16) | User created and saved a simulation in the SP-owned app; still listed after reload. `claims.sub` policies work on an SP-created backend. |

## Decision after the spike

- T1–T4 and T6 pass → build the pipeline (phase 1) with SP secret login.
- T6b passes → prefer OIDC; no client secret in GitHub.
- T3a duplicates instead of failing, or T4 fails → stop. The pipeline must
  then restore a `.deployments.json` from a secret store before each run.
- T8 fails → the prod item must be created by a user and only redeployed by
  the SP. Retest T3b on a user-created item.
