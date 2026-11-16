# CI/CD for a Microsoft Fabric App

A template repository that deploys one [Fabric App](https://learn.microsoft.com/fabric/apps/overview)
to one workspace per environment, with GitHub Actions or Azure Pipelines.
The developer deploys to dev from their machine; a pull request merged to
`main` deploys to prod. Make a repository from it, put your app in `app/`,
name your workspaces in `environments/`, and give each environment a
service principal.

Fabric Apps (preview) have no item definition to export or import yet, so
neither Fabric Git integration, deployment pipelines, `fabric-cicd` nor
`pyfabricops` can move them. Everything goes through the
[Rayfin CLI](https://learn.microsoft.com/fabric/apps/cli-reference)
(`rayfin up`), and this repository is the plumbing around it: which
workspace, which semantic models, which identity, and when.

```text
developer machine                         GitHub Actions / Azure Pipelines
─────────────────                         ────────────────────────────────
python scripts/deploy_app.py              pull request ──► CI: lint, types, tests, build
  --environment dev                                │ merge to main
        │                                          ▼
        │ service principal of dev        CD: scripts/deploy_app.py --environment prod
        ▼                                          │ service principal of prod
  workspace dev                                    ▼
  app ──reads──► semantic model (dev)       workspace prod
                                            app ──reads──► semantic model (prod)
```

```text
.
├── app/                              your Fabric App (a Rayfin project); ships a sample
├── environments/dev.yml              dev: workspace, app name, semantic models; no branch
├── environments/prod.yml             prod: the same, deployed from main
├── scripts/deploy_app.py             looks names up, binds the models, rayfin up, checks
├── scripts/environments.py           reads environments/, tells the environment of a branch
├── scripts/requirements.txt          pyfabricops, PyYAML, python-dotenv
├── .github/workflows/ci.yml          GitHub: checks every pull request
├── .github/workflows/deploy.yml      GitHub: deploys when a branch of an environment changes
├── azure-pipelines/ci.yml            Azure DevOps: the same checks
├── azure-pipelines/deploy.yml        Azure DevOps: the same deployment
├── docs/spike-phase-0.md             how each behaviour below was proven
└── .env.example                      the credentials of dev, for your machine
```

| Environment | File | Deployed by | Identity | Secret lives in |
| :--- | :--- | :--- | :--- | :--- |
| `dev` | `environments/dev.yml` | the developer, `python scripts/deploy_app.py --environment dev` | service principal of dev | `.env` on the developer's machine |
| `prod` | `environments/prod.yml` | the pipeline, on a merge to `main` | service principal of prod | the pipeline only |

## Why one service principal per environment

**Only the owner of a Fabric App can deploy to it.** Being Admin of the
workspace is not enough: `rayfin up` by anyone else fails with
`403 Forbidden: Only AppBackend artifact owner can perform this operation`.
The identity that first deploys an app becomes its owner, and stays so.

So each environment has its own service principal, which owns the app of
that environment:

- the service principal of **dev** is a Contributor of the dev workspace
  only; its secret is in `.env` on the machines of the developers, so any of
  them can deploy dev, and the app never depends on one person's account;
- the service principal of **prod** is a Contributor of the prod workspace
  only; its secret is only in the pipeline. Nobody can deploy prod from a
  laptop: the only way in is a pull request merged to `main`.

With one service principal for both, whoever has `.env` could run
`rayfin up` against prod and skip the pull request.

## What a deployment does

`scripts/deploy_app.py --environment <name>`, the same on a machine and in
a pipeline:

1. **Reads `environments/<name>.yml`** and checks every file: same semantic
   model aliases everywhere, no branch deploying to two environments.
2. **Looks the workspace and the semantic models up by name** with the
   service principal (`pyfabricops`). The files hold names, so the
   repository holds no ID of the tenant; an ID works too.
3. **Binds the app to the models of the environment.** A `dataapp`-style app
   reads semantic models through `@microsoft/fabric-app-data`, which writes
   their IDs into the bundle at build time (`src/fabric.generated.ts`). So
   nothing is promoted: each environment gets its own build, bound to its
   own models.
4. **Signs in to Rayfin as the service principal** and runs
   `rayfin up --workspace-id <id> --item-name <item> --yes`, which builds
   the app, syncs its settings, applies the database schema and uploads the
   bundle. With no `rayfin/.deployments.json`, as on a pipeline agent,
   `--yes` reuses the app of that name instead of creating a second one.
5. **Checks the deployed endpoint** (`rayfin up status`, HTTP 200).

`rayfin up` adds the hosting URL of the app to `allowedRedirectUris` in
`app/rayfin/rayfin.yml`; the script puts the file back as it was, so a
deployment leaves nothing to commit. Every deployment adds the URL again.
Nothing it prints holds an ID: the logs of a public repository are public.

## Set up a new repository

### 1. Make the repository

- **GitHub:** **Use this template** > **Create a new repository**.
- **Azure DevOps:** **Repos** > **Import repository**, with the clone URL of
  this repository.

### 2. Put your app in `app/`

`app/` ships a sample, the DRE Sankey app with write-back (synthetic data),
so that the pipelines run from the first commit. Replace its content with
your Rayfin project: `app/package.json` and `app/rayfin/rayfin.yml` must be
there. The pipelines run these scripts of `app/package.json`; rename them
in `ci.yml` if yours differ:

| Script | Used by | In the `dataapp` template |
| :--- | :--- | :--- |
| `npm run lint` | CI (not blocking) | yes |
| `npm test` | CI and CD | add [Vitest](https://vitest.dev) or remove the step |
| `npm run build:fabric` | CI; `rayfin up` runs `staticHosting.buildCommand` | yes |

`rayfin/rayfin.yml` needs `services.auth.fabric.enabled: true`: password
auth does not work on a deployed app. If the app reads semantic models
through `fabric-app-data`, keep `fabric.yaml` and `src/fabric.generated.ts`
in `app/.gitignore`: the script writes them per environment.

### 3. Create the workspaces

One workspace per environment, on a **Fabric capacity** (F SKU) in a
[region where Fabric Apps is available](https://learn.microsoft.com/fabric/admin/region-availability).
Fabric Apps do not run on Pro or shared capacity. In each one, publish the
semantic models the app reads, under the same names (from Power BI
Desktop, or with [pyfabricops-template](https://github.com/alisonpezzott/pyfabricops-template)).

A tenant admin enables, in the Fabric admin portal:

- **Fabric Apps (preview)**;
- **Service principals can use Fabric APIs** (or *can call Fabric public
  APIs*), for the service principals below or a security group of them;
- **Semantic Model Execute Queries REST API**, if the app reads semantic
  models.

### 4. Create the service principals

One per environment, in **Microsoft Entra ID** > **App registrations** >
**New registration** (single tenant, no redirect URI):

1. **Certificates & secrets** > **New client secret**. Copy the **Value**,
   not the *Secret ID*: it is shown once. Note when it expires.
2. **API permissions: add none.** Fabric does not use them; access comes
   from the tenant setting and the workspace role. Do not add Power BI
   Service permissions that need admin consent: they can make Fabric reject
   the service principal.
3. In its workspace only: **Manage access** > **Contributor**.

The service principal needs no access to the semantic models: the app
queries them as the signed-in user, so the users of the app need **Build**
and **Read** on them.

### 5. Name the workspaces in `environments/`

```yaml
# environments/prod.yml
workspace: my-app-prod        # name or ID
branch: main                  # the branch whose changes deploy here
item: my-app                  # the Fabric App item
semanticModels:               # by the alias the app code uses
  sales: Sales Model          # a model of the workspace above, name or ID
  # finance: { workspace: shared-models, model: Finance }   # another workspace
```

`dev.yml` has no `branch`: no pipeline deploys it. Every file binds the
same aliases; `python scripts/environments.py --check` validates them. The
file name is the environment name: lowercase letters, digits and `-`. More
environments, such as `test` from a `release/*` branch, are one file each,
plus their name in `azure-pipelines/deploy.yml`.

### 6. Deploy dev from your machine

Node.js 20, 22 or 24 (LTS) and Python 3.10 or later. Copy `.env.example` to
`.env` with the service principal of **dev**, then:

```bash
pip install -r scripts/requirements.txt
npm ci --prefix app
python scripts/deploy_app.py --environment dev
```

The first deployment creates the app, owned by the service principal of
dev. Open it from the workspace, in the Fabric portal. Apps bound to a
semantic model only work opened inside the portal for now.

### 7. Prepare the pipeline

**GitHub Actions**

1. **Settings** > **Environments** > **New environment** `prod`, with the
   secrets `FAB_TENANT_ID`, `FAB_CLIENT_ID` and `FAB_CLIENT_SECRET` of the
   service principal of **prod**, and **Deployment branches** limited to
   `main`.
2. **Settings** > **Branches** > protect `main`: require a pull request and
   the status check `check` (from `ci.yml`), and include administrators.
   On a private repository, branch protection and environment reviewers
   need a paid plan; on a public one they are free.

`ci.yml` runs on every pull request with no secret. `deploy.yml` runs when
`app/`, `environments/` or `scripts/` change on a branch of an environment.

**Azure Pipelines**

1. **Pipelines** > **Library** > variable group `fabric-prod` with
   `FAB-TENANT-ID`, `FAB-CLIENT-ID` and `FAB-CLIENT-SECRET` (secret) of the
   service principal of **prod**.
2. **Pipelines** > **Environments** > `prod`, with an **Exclusive Lock**
   check.
3. **Pipelines** > **New pipeline** > **Existing Azure Pipelines YAML file**,
   twice: `azure-pipelines/ci.yml` and `azure-pipelines/deploy.yml`. Allow
   the deploy pipeline to use the variable group and the environment.
4. On Azure Repos, add the CI pipeline as a **Build Validation** policy of
   `main`, and require a pull request to `main`.

### 8. Deploy prod

Open a pull request to `main`. When CI passes and it is merged, the
pipeline deploys prod. The first deployment creates the app, owned by the
service principal of prod.

## How you work

1. Change the app in `app/`; try it against dev with
   `python scripts/deploy_app.py --environment dev` (or `npm run dev` in
   `app/`, which runs the frontend locally against the deployed backend).
2. Commit to a branch and open a pull request to `main`. CI checks it.
3. Merge it: the pipeline deploys prod.

Never deploy prod by hand: you cannot, as only its service principal owns
it, and that one is only in the pipeline.

## Schema changes and data

The app's SQL database (when `services.data` is on) gets its schema from the
entities in `app/rayfin/data/`, applied by every `rayfin up`. Data never
moves between environments: each one keeps its own.

- **Adding** a table or a nullable column applies on the next deployment.
- **Dropping** or altering a column that loses data **fails the
  deployment** (`... would result in data loss but force mode is not
  enabled`). To apply it on purpose, run the deploy pipeline by hand on
  `main` with *Allow schema changes that lose data* (GitHub: `force`;
  Azure Pipelines: the `force` parameter), or locally with `--force` for
  dev.
- Rayfin sends the app settings before it applies the schema, so a refused
  deployment leaves the new settings with the previous bundle. Fix forward
  with a new pull request.

## Changing the owner of an app

There is no transfer of ownership: to give an app another owner, delete it
and deploy again with the new identity. Its SQL data is lost.

1. Delete the app in the workspace. It goes to the **workspace recycle
   bin**, with its SQL database, and **its name stays taken**: a deployment
   fails with `409 Conflict: An item with the name ... already exists`.
2. **Delete it permanently from the recycle bin.** Restoring it would bring
   back the old owner.
3. Deploy with the new identity.

## Troubleshooting

| Message | Cause | Fix |
| :--- | :--- | :--- |
| `403 Forbidden: Only AppBackend artifact owner can perform this operation` | The identity is not the owner of the app | Deploy with the service principal of that environment, or change the owner (above) |
| `409 Conflict: An item with the name ... already exists` | The app is in the recycle bin | Delete it permanently from the recycle bin |
| `A Rayfin item named ... already exists ... pass --yes` | `rayfin up` run by hand without `--yes` and without `.deployments.json` | Use `scripts/deploy_app.py`, which passes `--yes` |
| `OS keychain is not available and plaintext token storage is not enabled` | Linux agent without a keychain | `RAYFIN_ENCRYPTION_FALLBACK_ENABLED=true`, as the pipelines set |
| `Workspace ... not found, or the service principal is not a member of it` | Wrong name, missing role, or tenant setting off | Check the name, the Contributor role and *Service principals can use Fabric APIs* |
| `... would result in data loss but force mode is not enabled` | A schema change drops data | Review it, then deploy with force (above) |
| The app opens but its queries fail | Wrong model, or the user lacks Build and Read on it | Check `semanticModels` and the permissions of the users |

`rayfin up` writes detailed logs to `~/.rayfin/logs/`, also on failure.

## Limits

- Fabric Apps is in preview, and so is the Rayfin CLI: the version is the
  one in `app/package-lock.json`. Check the
  [docs](https://learn.microsoft.com/fabric/apps/) before upgrading it.
- The service principal signs in with a client secret. A federated
  credential (OIDC) through `RAYFIN_TOKEN` would remove the secret from the
  pipeline; it is not wired yet.
- Semantic models are bound through `fabric-app-data`. Rayfin connectors
  (`rayfin connector add`) deploy with `rayfin up` too, but are not covered
  by the lookups of the script.
- The developers of dev share one service principal: the Fabric audit log
  shows the service principal, not who ran the deployment.

## The sample app

`app/` holds a Fabric App over a synthetic company (Blacksmith): its income
statement as a Sankey from revenue to profit, KPIs, trend and a write-back
of what-if simulations to the app's SQL database. Its semantic model,
`dre-sankey`, is published from
[fabric-app-dre-sankey](https://github.com/alisonpezzott/fabric-app-dre-sankey).
All data is synthetic.
