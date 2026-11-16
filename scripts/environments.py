"""
The environments the app deploys to, one YAML file each.

Each file of ``environments`` is an environment, named after it
(``environments/prod.yml`` is ``prod``), and says everything that tells it
apart from the others: its workspace, the branch that deploys to it, the
name of the Fabric App item and the semantic models the app reads. So the
pipelines hold no environment of their own: they ask which environment a
branch deploys to.

Run as a script, it answers the pipelines without pyfabricops::

    python scripts/environments.py --branch main     # prints prod, or nothing
    python scripts/environments.py --check           # validates every file
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# The YAML file of each environment, in the environments folder at the root
# of the repository, one folder above this script.
ENVIRONMENTS = Path(__file__).resolve().parents[1] / "environments"
SUFFIX = ".yml"
KEYS = {"workspace", "branch", "item", "semanticModels"}
# Lowercase letters, digits and "-": the same name for the file, the
# environment of GitHub or Azure DevOps and --environment, on any system.
NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
# An alias of fabric.yaml, as the app code uses it.
ALIAS = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class SemanticModel:
    """A semantic model the app reads, by name or ID."""

    workspace: str
    model: str


@dataclass(frozen=True)
class Environment:
    """An environment, as its file in ``environments`` describes it."""

    name: str
    file: Path
    workspace: str
    # The branch that deploys to it, as a name or a glob; None: no pipeline.
    branch: str | None
    item: str
    # The semantic models by the alias the app code uses.
    semantic_models: Mapping[str, SemanticModel]

    def deploys(self, branch: str) -> bool:
        """Whether a branch deploys to this environment."""
        return self.branch is not None and fnmatch.fnmatchcase(branch, self.branch)


def _text(value: Any) -> bool:
    """Whether a YAML value is a non-empty string."""
    return isinstance(value, str) and bool(value.strip())


def _semantic_models(
    file: Path, raw: Any, workspace: str
) -> tuple[dict[str, SemanticModel], list[str]]:
    """The semantic models of a file, and what is wrong with them."""
    if raw is None:
        return {}, []
    if not isinstance(raw, dict):
        return {}, [f"{file.name}: semanticModels is not a mapping."]
    models: dict[str, SemanticModel] = {}
    problems: list[str] = []
    for alias, value in raw.items():
        if not isinstance(alias, str) or not ALIAS.fullmatch(alias):
            problems.append(
                f"{file.name}: semantic model alias {alias!r} is not an identifier."
            )
            continue
        if _text(value):
            models[alias] = SemanticModel(workspace, str(value))
        elif (
            isinstance(value, dict)
            and _text(value.get("model"))
            and set(value) <= {"workspace", "model"}
            and (value.get("workspace") is None or _text(value["workspace"]))
        ):
            models[alias] = SemanticModel(
                str(value.get("workspace") or workspace), str(value["model"])
            )
        else:
            problems.append(
                f"{file.name}: semantic model {alias} is neither a name or "
                "ID nor { workspace, model }."
            )
    return models, problems


def read_environment(name: str) -> Environment:
    """The environment of a name; exits when its file is missing or wrong."""
    file = ENVIRONMENTS / f"{name}{SUFFIX}"
    if not file.is_file():
        known = ", ".join(names())
        raise SystemExit(f"No environment {name}: {file} is missing. Known: {known}.")
    content = yaml.safe_load(file.read_text(encoding="utf-8-sig")) or {}
    if not isinstance(content, dict):
        raise SystemExit(f"{file.name}: not a mapping of keys.")
    problems = [
        f"{file.name}: unknown key {key}."
        for key in sorted(set(map(str, content)) - KEYS)
    ]
    if not NAME.fullmatch(name):
        problems.append(
            f"{file.name}: name an environment with lowercase letters, digits and '-'."
        )
    workspace = content.get("workspace")
    branch = content.get("branch")
    item = content.get("item")
    if not _text(workspace):
        problems.append(f"{file.name}: no workspace.")
    if branch is not None and not _text(branch):
        problems.append(f"{file.name}: branch is not a name.")
    if not _text(item):
        problems.append(f"{file.name}: no item, the name of the Fabric App.")
    models, wrong = _semantic_models(
        file, content.get("semanticModels"), str(workspace)
    )
    problems += wrong
    if problems:
        raise SystemExit("\n".join(problems))
    return Environment(
        name=name,
        file=file,
        workspace=str(workspace),
        branch=branch,
        item=str(item),
        semantic_models=models,
    )


def names() -> list[str]:
    """The names of the environments, from their files."""
    return sorted(path.stem for path in ENVIRONMENTS.glob(f"*{SUFFIX}"))


def read_all() -> list[Environment]:
    """Every environment; exits when one is wrong or two share a branch."""
    environments = [read_environment(name) for name in names()]
    if not environments:
        raise SystemExit(f"No environment: {ENVIRONMENTS} has no *{SUFFIX}.")
    problems: list[str] = []
    branches: dict[str, str] = {}
    for environment in environments:
        if environment.branch is None:
            continue
        other = branches.setdefault(environment.branch, environment.name)
        if other != environment.name:
            problems.append(
                f"{environment.file.name}: branch {environment.branch} "
                f"already deploys to {other}."
            )
    aliases = {env.name: set(env.semantic_models) for env in environments}
    if len({frozenset(a) for a in aliases.values()}) > 1:
        listed = "; ".join(
            f"{name}: {', '.join(sorted(a)) or 'none'}" for name, a in aliases.items()
        )
        problems.append(
            "The environments do not bind the same semantic model aliases, "
            f"which the app code needs in all of them ({listed})."
        )
    if problems:
        raise SystemExit("\n".join(problems))
    return environments


def of_branch(branch: str) -> Environment | None:
    """The environment a branch deploys to, if any."""
    # refs/heads/main, as Azure DevOps gives it, is main.
    branch = branch.removeprefix("refs/heads/")
    matches = [env for env in read_all() if env.deploys(branch)]
    if len(matches) > 1:
        listed = ", ".join(env.name for env in matches)
        raise SystemExit(f"Branch {branch} deploys to more than one: {listed}.")
    return matches[0] if matches else None


def main(argv: Sequence[str] | None = None) -> int:
    """Answers the pipelines: the environment of a branch, or a check."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--branch", help="prints the environment this branch deploys to")
    group.add_argument(
        "--check", action="store_true", help="validates every environment"
    )
    args = parser.parse_args(argv)
    if args.check:
        for environment in read_all():
            target = environment.branch or "no pipeline"
            print(f"{environment.name}: {target}")
        return 0
    target_environment = of_branch(args.branch)
    print(target_environment.name if target_environment else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
