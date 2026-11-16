"""
Deploys the Fabric App in app/ to an environment of environments/.

Fabric Apps have no item definition to export or import yet, so the Rayfin
CLI is the only way in. This script wraps it the same way locally and in a
pipeline:

1. looks up the workspace and the semantic models of the environment by
   name, with the service principal of .env or of the pipeline;
2. binds the app to those models (``fabric-app-data``: the IDs go into the
   bundle at build time, so each environment gets its own build);
3. signs in to Rayfin as the service principal and runs ``rayfin up``,
   which builds, applies the schema and uploads the bundle;
4. checks the deployed endpoint.

::

    python scripts/deploy_app.py --environment dev
    python scripts/deploy_app.py --environment prod --force     # drops data
    python scripts/deploy_app.py --environment prod --bind-only --placeholder

The service principal is FAB_TENANT_ID, FAB_CLIENT_ID and FAB_CLIENT_SECRET,
from the environment or from .env at the root. Only the owner of a Fabric
App can deploy to it, so use the service principal of that environment.

Nothing it prints holds an ID: the logs of a public repository are public.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from environments import Environment, read_all, read_environment

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
RAYFIN_YML = APP / "rayfin" / "rayfin.yml"
GENERATED = "src/fabric.generated.ts"
PLACEHOLDER = "00000000-0000-0000-0000-000000000000"
GUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
CREDENTIALS = ("FAB_TENANT_ID", "FAB_CLIENT_ID", "FAB_CLIENT_SECRET")


def hide(text: str) -> str:
    """The text without IDs or secrets, fit for a public log."""
    for name in ("FAB_CLIENT_SECRET",):
        secret = os.environ.get(name)
        if secret:
            text = text.replace(secret, "***")
    return GUID.sub("<id>", text)


def npx(*args: str, capture: bool = True) -> subprocess.CompletedProcess[str]:
    """Runs an npm package binary of the app, from app/."""
    executable = shutil.which("npx")
    if executable is None:
        raise SystemExit("npx not found: install Node.js 20, 22 or 24.")
    return subprocess.run(
        [executable, *args],
        cwd=APP,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def run(*args: str) -> str:
    """Runs npx and returns its output; exits, hiding IDs, when it fails."""
    result = npx(*args)
    if result.returncode != 0:
        print(hide(result.stdout + result.stderr).strip(), file=sys.stderr)
        raise SystemExit(f"npx {args[0]} {args[1]} failed.")
    return result.stdout


def credentials() -> None:
    """Loads .env, when there is one, and checks the service principal."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        pass
    else:
        load_dotenv(ROOT / ".env")
    missing = [name for name in CREDENTIALS if not os.environ.get(name)]
    if missing:
        raise SystemExit(
            f"Missing {', '.join(missing)}: set them in .env (see "
            ".env.example) or in the pipeline."
        )


def resolve(environment: Environment) -> tuple[str, dict[str, tuple[str, str]]]:
    """The workspace ID and, by alias, the workspace and model IDs."""
    import pyfabricops as pf  # type: ignore[import-untyped]

    pf.set_auth_provider("env")
    workspace_id = pf.resolve_workspace(environment.workspace)
    if workspace_id is None:
        raise SystemExit(
            f"Workspace {environment.workspace} not found, or the service "
            "principal is not a member of it."
        )
    models: dict[str, tuple[str, str]] = {}
    for alias, model in environment.semantic_models.items():
        model_workspace = pf.resolve_workspace(model.workspace)
        if model_workspace is None:
            raise SystemExit(
                f"{alias}: workspace {model.workspace} not found, or the "
                "service principal is not a member of it."
            )
        model_id = (
            model.model
            if GUID.fullmatch(model.model)
            else pf.get_semantic_model_id(model_workspace, model.model)
        )
        if model_id is None:
            raise SystemExit(
                f"{alias}: semantic model {model.model} not found in {model.workspace}."
            )
        models[alias] = (str(model_workspace), str(model_id))
    return str(workspace_id), models


def bind(profile: str, models: Mapping[str, tuple[str, str]]) -> None:
    """Writes the profile of fabric.yaml and regenerates the TS config."""
    if not models:
        # An app that reads no semantic model has no fabric-app-data.
        print("No semantic model to bind.")
        return
    if not (APP / "fabric.yaml").is_file():
        run("fabric-app-data", "init")
    for alias, (workspace_id, model_id) in models.items():
        run(
            "fabric-app-data",
            "add",
            "semanticModel",
            alias,
            "-w",
            workspace_id,
            "-i",
            model_id,
            "-p",
            profile,
        )
    run("fabric-app-data", "use", profile, "-o", GENERATED)
    print(f"Bound {', '.join(models) or 'no model'} (profile {profile}).")


def login() -> None:
    """Signs in to Rayfin as the service principal."""
    result = npx(
        "rayfin",
        "login",
        "--service-principal",
        "--client-id",
        os.environ["FAB_CLIENT_ID"],
        "--client-secret",
        os.environ["FAB_CLIENT_SECRET"],
        "--tenant",
        os.environ["FAB_TENANT_ID"],
    )
    if result.returncode != 0:
        print(hide(result.stdout + result.stderr).strip(), file=sys.stderr)
        raise SystemExit("Rayfin sign-in as the service principal failed.")
    print("Signed in to Rayfin as the service principal.")


def up(environment: Environment, workspace_id: str, force: bool) -> None:
    """Runs rayfin up; exits with Rayfin's own message when it fails."""
    args = [
        "rayfin",
        "up",
        "--workspace-id",
        workspace_id,
        "--item-name",
        environment.item,
        "--yes",
        "--output",
        "json",
    ]
    if force:
        args.append("--force")
    # rayfin up adds the hosting URL of the app to allowedRedirectUris in
    # rayfin.yml. Put the file back as it was, so that a local deployment
    # leaves no change to commit; every deployment adds it again.
    original = RAYFIN_YML.read_bytes()
    try:
        result = npx(*args)
    finally:
        RAYFIN_YML.write_bytes(original)
    outcome: dict[str, Any] = {}
    try:
        outcome = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        pass
    if result.returncode != 0 or outcome.get("status") != "success":
        message = outcome.get("error") or result.stdout + result.stderr
        print(hide(str(message)).strip(), file=sys.stderr)
        raise SystemExit(f"rayfin up failed for {environment.name}.")
    for warning in outcome.get("warnings") or []:
        print(f"warning: {hide(str(warning))}")
    print(f"Deployed {environment.item} to {environment.name}.")


def status() -> None:
    """Exits when the management endpoint of the app does not answer 200."""
    result = npx("rayfin", "up", "status", "--json")
    try:
        health = json.loads(result.stdout).get("endpointHealth") or {}
    except json.JSONDecodeError:
        health = {}
    if health.get("httpStatus") != 200:
        error = health.get("error") or health.get("errorCode") or "no answer"
        raise SystemExit(f"Endpoint check failed: {hide(str(error))}.")
    print("Endpoint check: 200.")


def main(argv: Sequence[str] | None = None) -> int:
    """Deploys the app to the environment given."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--environment", required=True)
    parser.add_argument(
        "--bind-only",
        action="store_true",
        help="binds the semantic models and stops: no sign-in, no deploy",
    )
    parser.add_argument(
        "--placeholder",
        action="store_true",
        help="with --bind-only: placeholder IDs, no lookup (CI)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="allows schema changes that lose data (rayfin up --force)",
    )
    args = parser.parse_args(argv)
    if args.placeholder and not args.bind_only:
        parser.error("--placeholder needs --bind-only.")
    read_all()  # every file valid, same aliases everywhere
    environment = read_environment(args.environment)
    if args.placeholder:
        bind("ci", {a: (PLACEHOLDER, PLACEHOLDER) for a in environment.semantic_models})
        return 0
    credentials()
    workspace_id, models = resolve(environment)
    bind(environment.name, models)
    if args.bind_only:
        return 0
    login()
    up(environment, workspace_id, args.force)
    status()
    return 0


if __name__ == "__main__":
    sys.exit(main())
