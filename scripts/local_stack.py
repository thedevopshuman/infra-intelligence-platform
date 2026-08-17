#!/usr/bin/env python3
"""Secure, one-command lifecycle for the durable local Docker stack."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import stat
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".iip"
ENV_PATH = STATE_DIR / "local.env"
CREDENTIALS_PATH = STATE_DIR / "local-credentials.json"
COMPOSE_PATH = ROOT / "deploy" / "docker-compose.yml"
PROJECT = "iip-local"
_LOCAL_OPERATOR_ROLES = ("developer", "platform-admin")


def _local_signal_catalog_json() -> str:
    """Return a non-secret reviewed profile for the disposable local tenant."""

    document = {
        "apiVersion": "iip.platform/v1alpha1",
        "kind": "InvestigationSignalCatalog",
        "profiles": [
            {
                "tenantId": "local",
                "profileId": "local-kubernetes",
                "version": "1.0.0",
                "selections": {
                    "changeSelections": [
                        {
                            "id": "cqs_6a28c9f31db44ea2",
                            "integrationId": "platform-resource-history",
                            "rootCauseClasses": [
                                "kubernetes.rollout.unavailable-replicas"
                            ],
                            "query": {"changeKinds": ["image", "scale"]},
                            "limits": {
                                "maxChanges": 100,
                                "maxObservationsPerResource": 500,
                                "maxBytes": 524288,
                            },
                            "interpretation": {
                                "minChanges": 1,
                                "whenMatched": "supports",
                                "whenNotMatched": "neutral",
                            },
                        }
                    ]
                },
            }
        ],
    }
    return json.dumps(document, separators=(",", ":"))


def _token_identity(actor_id: str, roles: Sequence[str]) -> tuple[dict[str, object], dict[str, object]]:
    token = secrets.token_hex(32)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    verifier = {
        "tokenSha256": f"sha256:{digest}",
        "actorId": actor_id,
        "tenantId": "local",
        "roles": list(roles),
    }
    credential = {
        "actorId": actor_id,
        "tenantId": "local",
        "roles": list(roles),
        "bearerToken": token,
    }
    return verifier, credential


def create_local_configuration() -> tuple[Path, Path, bool]:
    """Create protected local-only configuration, or preserve the existing pair."""

    if ENV_PATH.exists() and CREDENTIALS_PATH.exists():
        _ensure_local_operator_roles()
        _ensure_local_signal_catalog()
        return ENV_PATH, CREDENTIALS_PATH, False
    if ENV_PATH.exists() or CREDENTIALS_PATH.exists():
        raise RuntimeError("local configuration is incomplete; restore or remove both .iip files")

    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    identities: list[dict[str, object]] = []
    credentials: list[dict[str, object]] = []
    for actor, roles in (
        ("local-operator", _LOCAL_OPERATOR_ROLES),
        ("local-approver", ("approver",)),
        ("local-executor", ("executor",)),
    ):
        verifier, credential = _token_identity(actor, roles)
        identities.append(verifier)
        credentials.append(credential)

    identity_document = json.dumps({"identities": identities}, separators=(",", ":"))
    environment = "\n".join(
        (
            f"COMPOSE_PROJECT_NAME={PROJECT}",
            f"IIP_POSTGRES_PASSWORD={secrets.token_hex(24)}",
            f"IIP_AUTH_IDENTITIES_JSON={identity_document}",
            f"IIP_INVESTIGATION_SIGNAL_CATALOG_JSON={_local_signal_catalog_json()}",
            "",
        )
    )
    credential_document = {
        "consoleUrl": "http://127.0.0.1:8080/console",
        "tenantId": "local",
        "identities": credentials,
    }

    ENV_PATH.write_text(environment, encoding="utf-8")
    CREDENTIALS_PATH.write_text(
        json.dumps(credential_document, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(ENV_PATH, stat.S_IRUSR | stat.S_IWUSR)
    os.chmod(CREDENTIALS_PATH, stat.S_IRUSR | stat.S_IWUSR)
    return ENV_PATH, CREDENTIALS_PATH, True


def _ensure_local_operator_roles() -> None:
    """Add the non-secret local operations role to configurations from older releases."""

    try:
        credentials = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
        environment_lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
        identity_index = next(
            index
            for index, line in enumerate(environment_lines)
            if line.startswith("IIP_AUTH_IDENTITIES_JSON=")
        )
        verifiers = json.loads(environment_lines[identity_index].partition("=")[2])
        credential_identities = credentials["identities"]
        verifier_identities = verifiers["identities"]
        credential = next(
            item
            for item in credential_identities
            if item.get("actorId") == "local-operator"
        )
        verifier = next(
            item
            for item in verifier_identities
            if item.get("actorId") == "local-operator"
        )
        if not isinstance(credential.get("roles"), list) or not isinstance(
            verifier.get("roles"), list
        ):
            raise TypeError
    except (OSError, json.JSONDecodeError, KeyError, StopIteration, TypeError) as exc:
        raise RuntimeError("local configuration is unreadable") from exc

    expected = list(_LOCAL_OPERATOR_ROLES)
    if credential["roles"] == expected and verifier["roles"] == expected:
        return
    if set(credential["roles"]) != {"developer"} or set(verifier["roles"]) != {
        "developer"
    }:
        raise RuntimeError("local operator roles do not match a supported release")
    credential["roles"] = expected
    verifier["roles"] = expected
    environment_lines[identity_index] = "IIP_AUTH_IDENTITIES_JSON=" + json.dumps(
        verifiers,
        separators=(",", ":"),
    )
    ENV_PATH.write_text("\n".join(environment_lines) + "\n", encoding="utf-8")
    CREDENTIALS_PATH.write_text(
        json.dumps(credentials, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(ENV_PATH, stat.S_IRUSR | stat.S_IWUSR)
    os.chmod(CREDENTIALS_PATH, stat.S_IRUSR | stat.S_IWUSR)


def _ensure_local_signal_catalog() -> None:
    """Add the reviewed local profile to configurations from older releases."""

    try:
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RuntimeError("local configuration is unreadable") from exc
    if any(
        line.startswith("IIP_INVESTIGATION_SIGNAL_CATALOG_JSON=")
        for line in lines
    ):
        return
    lines.append(
        "IIP_INVESTIGATION_SIGNAL_CATALOG_JSON=" + _local_signal_catalog_json()
    )
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(ENV_PATH, stat.S_IRUSR | stat.S_IWUSR)


def load_credentials() -> Mapping[str, object]:
    if not CREDENTIALS_PATH.is_file():
        raise RuntimeError("local credentials do not exist; run make dev-up first")
    try:
        document = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("local credentials are unreadable") from exc
    if not isinstance(document, dict):
        raise RuntimeError("local credentials are invalid")
    return document


def compose(arguments: Sequence[str]) -> None:
    command = [
        os.environ.get("IIP_DOCKER_BIN", "docker"),
        "compose",
        "--project-name",
        PROJECT,
        "--env-file",
        str(ENV_PATH),
        "--file",
        str(COMPOSE_PATH),
        *arguments,
    ]
    try:
        subprocess.run(command, cwd=ROOT, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError("Docker CLI is unavailable; start Docker Desktop and retry") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Docker Compose failed with exit code {exc.returncode}") from exc


def print_credentials(*, include_workflow_identities: bool = False) -> None:
    document = load_credentials()
    identities = document.get("identities")
    if not isinstance(identities, list) or not identities or not isinstance(identities[0], dict):
        raise RuntimeError("local credentials are invalid")
    print(f"Console: {document.get('consoleUrl')}")
    visible = identities if include_workflow_identities else identities[:1]
    for identity in visible:
        actor_id = identity.get("actorId")
        roles = ", ".join(identity.get("roles", []))
        print(f"{actor_id} ({roles}) Bearer token: {identity.get('bearerToken')}")
    print(f"Protected credentials: {CREDENTIALS_PATH}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "up", "status", "credentials", "down"))
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            _, _, created = create_local_configuration()
            print("Created protected local configuration." if created else "Local configuration already exists.")
            print_credentials()
        elif args.command == "up":
            _, _, created = create_local_configuration()
            if created:
                print("Created protected local configuration.")
            compose(
                (
                    "up",
                    "--build",
                    "--detach",
                    "--remove-orphans",
                    "--wait",
                    "--wait-timeout",
                    "180",
                )
            )
            print("Infrastructure Intelligence local stack is ready in Docker Desktop.")
            print_credentials()
        elif args.command == "status":
            if not ENV_PATH.is_file():
                raise RuntimeError("local configuration does not exist; run make dev-up first")
            compose(("ps",))
        elif args.command == "credentials":
            print_credentials(include_workflow_identities=True)
        else:
            if not ENV_PATH.is_file():
                raise RuntimeError("local configuration does not exist; nothing to stop")
            compose(("down",))
            print("Stopped the local stack. The PostgreSQL volume was preserved.")
        return 0
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
