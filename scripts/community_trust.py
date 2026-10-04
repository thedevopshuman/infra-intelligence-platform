"""Explicit offline trust rotation for the persistent community installation.

This host-side lifecycle never starts/stops services or changes external trust,
tenant configuration, passwords, tokens, or application data. The local issuer
key is discarded: every generation replaces the root and all five leaf pairs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import community_stack as stack
import community_transport as transport

SHA = re.compile(r"[a-f0-9]{64}")
IDENTIFIER = re.compile(r"[a-f0-9]{32}")
MAX_GENERATIONS = 64
MAX_HISTORY = 256
STATE_FILE = "transport-state.json"
GENERATIONS = "transport-generations"
OVERLAP = "transport-overlap"
STARTED = "transport-started.json"


class TrustError(ValueError):
    """A value-minimized operational failure."""


def _read(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_size > 65536):
            raise TrustError("community.trust.file-invalid")
        value = source.read(65537)
        if len(value) != info.st_size:
            raise TrustError("community.trust.file-invalid")
        return value


def generation_digest(directory: Path) -> str:
    stack._private_directory(directory)
    if set(path.name for path in directory.iterdir()) != set(transport.TRANSPORT_FILES):
        raise TrustError("community.trust.generation-invalid")
    values = {name: hashlib.sha256(_read(directory / name)).hexdigest()
              for name in transport.TRANSPORT_FILES}
    return hashlib.sha256(stack.compact(values).encode()).hexdigest()


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date(value) -> bool:
    try:
        return isinstance(value, str) and datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


def _hash(value) -> bool:
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def _binding(state: Path) -> str:
    # Configuration/credential changes are a separate workflow. Runtime health
    # receipts and image observations may change after an explicit startup.
    return hashlib.sha256(stack.compact({name: hashlib.sha256(_read(state / name)).hexdigest()
        for name in ("installation.json", "credentials.json", "daemon.json")}).encode()).hexdigest()


def _document(state: Path) -> dict | None:
    stack._private_directory(state)
    path = state / STATE_FILE
    if not path.exists() and not path.is_symlink():
        return None
    value = stack.read_protected(path)
    if (set(value) != {"format", "active", "rotation", "history"}
            or type(value["format"]) is not int or value["format"] != 1
            or not _hash(value["active"]) or not isinstance(value["history"], list)
            or not 1 <= len(value["history"]) <= MAX_HISTORY):
        raise TrustError("community.trust.state-invalid")
    for event in value["history"]:
        if (not isinstance(event, dict) or set(event) != {"at", "action", "rotationId", "from", "to"}
                or not _date(event["at"]) or event["action"] not in ("prepare", "activate", "rollback", "finalize", "cancel")
                or not isinstance(event["rotationId"], str) or not IDENTIFIER.fullmatch(event["rotationId"])
                or not _hash(event["from"]) or not _hash(event["to"]) or event["from"] == event["to"]):
            raise TrustError("community.trust.history-invalid")
    rotation = value["rotation"]
    if rotation is not None:
        if (not isinstance(rotation, dict)
                or set(rotation) != {"id", "from", "to", "phase", "createdAt", "binding", "overlapSha256"}
                or not isinstance(rotation["id"], str) or not IDENTIFIER.fullmatch(rotation["id"])
                or not _hash(rotation["from"]) or not _hash(rotation["to"]) or rotation["from"] == rotation["to"]
                or rotation["phase"] not in ("prepared", "activated", "rolled-back")
                or not _date(rotation["createdAt"]) or not _hash(rotation["binding"])
                or not _hash(rotation["overlapSha256"])
                or value["active"] != rotation["to" if rotation["phase"] == "activated" else "from"]):
            raise TrustError("community.trust.rotation-invalid")
    # Reconcile the pointer with its complete, bounded transition history.
    # A syntactically valid edit cannot silently reactivate a retired CA.
    active = None
    pending = None
    seen = set()
    for event in value["history"]:
        action = event["action"]
        identity = (event["rotationId"], event["from"], event["to"])
        if action == "prepare":
            if pending or event["rotationId"] in seen or (active is not None and active != event["from"]):
                raise TrustError("community.trust.history-invalid")
            seen.add(event["rotationId"])
            pending = (identity, "prepared")
            active = event["from"]
        else:
            if pending is None or pending[0] != identity:
                raise TrustError("community.trust.history-invalid")
            phase = pending[1]
            if action == "activate" and phase == "prepared":
                pending, active = (identity, "activated"), event["to"]
            elif action == "rollback" and phase == "activated":
                pending, active = (identity, "rolled-back"), event["from"]
            elif (action == "cancel" and phase == "prepared") or (action == "finalize" and phase in ("activated", "rolled-back")):
                pending = None
            else:
                raise TrustError("community.trust.history-invalid")
    expected = None if rotation is None else ((rotation["id"], rotation["from"], rotation["to"]), rotation["phase"])
    if pending != expected or active != value["active"]:
        raise TrustError("community.trust.history-invalid")
    return value


def _generation(state: Path, identifier: str, *, current: bool) -> Path:
    if not _hash(identifier):
        raise TrustError("community.trust.generation-invalid")
    stack._private_directory(state / GENERATIONS)
    path = state / GENERATIONS / identifier
    if generation_digest(path) != identifier:
        raise TrustError("community.trust.generation-invalid")
    transport.validate_transport_directory(path, require_current=current)
    return path


def _rotation_valid(state: Path, document: dict) -> None:
    rotation = document["rotation"]
    if rotation is None:
        return
    if _binding(state) != rotation["binding"]:
        raise TrustError("community.trust.source-changed")
    old = _generation(state, rotation["from"], current=False)
    new = _generation(state, rotation["to"], current=False)
    stack._private_directory(state / OVERLAP)
    bundle = _read(state / OVERLAP / (rotation["id"] + ".crt"))
    expected = _read(old / "ca.crt") + _read(new / "ca.crt")
    if bundle != expected or hashlib.sha256(bundle).hexdigest() != rotation["overlapSha256"]:
        raise TrustError("community.trust.overlap-invalid")


def selected_transport(state: Path, *, require_current: bool = True, for_startup: bool = False) -> tuple[Path, str]:
    document = _document(state)
    if document is None:
        directory = state / "transport"
        transport.validate_transport_directory(directory, require_current=require_current)
        return directory, generation_digest(directory)
    _rotation_valid(state, document)
    if for_startup and document["rotation"] and document["rotation"]["phase"] == "prepared":
        raise TrustError("community.trust.activation-or-cancel-required")
    return _generation(state, document["active"], current=require_current), document["active"]


def require_settled(state: Path) -> None:
    document = _document(state)
    if document is not None and document["rotation"] is not None:
        raise TrustError("community.trust.finalize-or-cancel-required")


def _directory(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        path.mkdir(mode=0o700)
        stack._sync_directory(path.parent)
    stack._private_directory(path)


def _write(path: Path, value: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def _atomic(path: Path, value: dict) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".transport-state-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(stack.compact(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        stack._sync_directory(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def _publish_generation(state: Path, source: Path) -> str:
    identifier = generation_digest(source)
    transport.validate_transport_directory(source, require_current=False)
    root = state / GENERATIONS
    _directory(root)
    destination = root / identifier
    if destination.exists() or destination.is_symlink():
        _generation(state, identifier, current=False)
        return identifier
    if len(list(root.iterdir())) >= MAX_GENERATIONS:
        raise TrustError("community.trust.generation-limit")
    with tempfile.TemporaryDirectory(prefix=".transport-copy-", dir=state) as temporary:
        staging = Path(temporary) / "generation"
        staging.mkdir(mode=0o700)
        for name in transport.TRANSPORT_FILES:
            _write(staging / name, _read(source / name))
        if generation_digest(staging) != identifier:
            raise TrustError("community.trust.source-changed")
        stack._sync_directory(staging)
        os.rename(staging, destination)
        stack._sync_directory(root)
    return identifier


def _event(document: dict, action: str) -> None:
    if len(document["history"]) >= MAX_HISTORY:
        raise TrustError("community.trust.history-limit")
    rotation = document["rotation"]
    document["history"].append({"at": _stamp(), "action": action,
        "rotationId": rotation["id"], "from": rotation["from"], "to": rotation["to"]})


def _offline(state: Path):
    from community_recovery import RecoveryDocker

    stack.require_complete_recovery(state)
    stack.installed_environment(state, validate_contracts=False)
    docker = RecoveryDocker(state)
    docker.require_stopped(stack.project_name(state))
    return docker


def prepare(state: Path) -> None:
    _offline(state)
    document = _document(state)
    if document and document["rotation"]:
        _rotation_valid(state, document)
        if document["rotation"]["phase"] == "prepared":
            return
        raise TrustError("community.trust.finalize-required")
    source, _ = selected_transport(state, require_current=False)
    old = _publish_generation(state, source)
    if document and len(document["history"]) > MAX_HISTORY - 5:
        raise TrustError("community.trust.history-limit")
    binding = _binding(state)
    with tempfile.TemporaryDirectory(prefix=".transport-prepare-", dir=state) as temporary:
        staging = Path(temporary)
        transport.write_transport(staging)
        transport.validate_transport_directory(staging / "transport")
        new = _publish_generation(state, staging / "transport")
    identifier = secrets.token_hex(16)
    _directory(state / OVERLAP)
    overlap = _read(_generation(state, old, current=False) / "ca.crt") + _read(_generation(state, new, current=True) / "ca.crt")
    _write(state / OVERLAP / (identifier + ".crt"), overlap)
    stack._sync_directory(state / OVERLAP)
    document = document or {"format": 1, "active": old, "rotation": None, "history": []}
    document["rotation"] = {"id": identifier, "from": old, "to": new, "phase": "prepared",
        "createdAt": _stamp(), "binding": binding, "overlapSha256": hashlib.sha256(overlap).hexdigest()}
    _event(document, "prepare")
    _offline(state)
    if _binding(state) != binding:
        raise TrustError("community.trust.source-changed")
    _atomic(state / STATE_FILE, document)


def switch(state: Path, action: str, *, external_trust_staged: bool) -> None:
    if action not in ("activate", "rollback") or not external_trust_staged:
        raise TrustError("community.trust.external-trust-acknowledgement-required")
    _offline(state)
    document = _document(state)
    if not document or not document["rotation"]:
        raise TrustError("community.trust.rotation-required")
    _rotation_valid(state, document)
    rotation = document["rotation"]
    target_phase = "activated" if action == "activate" else "rolled-back"
    if rotation["phase"] == target_phase:
        return
    if rotation["phase"] != ("prepared" if action == "activate" else "activated"):
        raise TrustError("community.trust.phase-invalid")
    target = rotation["to" if action == "activate" else "from"]
    _generation(state, target, current=True)
    rotation["phase"] = target_phase
    document["active"] = target
    _event(document, action)
    _atomic(state / STATE_FILE, document)


def cancel(state: Path) -> None:
    _offline(state)
    document = _document(state)
    if not document:
        raise TrustError("community.trust.rotation-required")
    if document["rotation"] is None and document["history"][-1]["action"] == "cancel":
        return
    if not document["rotation"] or document["rotation"]["phase"] != "prepared":
        raise TrustError("community.trust.phase-invalid")
    _rotation_valid(state, document)
    _event(document, "cancel")
    document["rotation"] = None
    _atomic(state / STATE_FILE, document)


def _healthy_containers(docker, project: str) -> list[str]:
    from community_recovery import SERVICES

    identifiers = docker.containers(project)
    observed = set()
    for identifier in identifiers:
        values = docker.text("inspect", "--format",
            '{{index .Config.Labels "com.docker.compose.service"}}|{{.State.Status}}|{{.State.ExitCode}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}', identifier).split("|")
        if len(values) != 4 or values[0] not in SERVICES or values[0] in observed:
            raise TrustError("community.trust.runtime-invalid")
        service, status, exit_code, health = values
        if service in ("initialize", "migrate"):
            valid = status == "exited" and exit_code == "0"
        else:
            valid = status == "running" and (health == "healthy" or (service == "otel-collector" and health == ""))
        if not valid:
            raise TrustError("community.trust.runtime-unhealthy")
        observed.add(service)
    if observed != set(SERVICES):
        raise TrustError("community.trust.runtime-incomplete")
    return sorted(identifiers)


def guard_start(state: Path, arguments, values: dict[str, str]) -> bool:
    """A repeated healthy up is read-only; repair/build requires a full stop.

    Compose may rerun an exited one-shot initializer even when serving services
    are already healthy. Reprojecting TLS files then would truncate live trust
    files. These immutable labels bind the completed initializer to the desired
    exact installation and operational files before permitting an up no-op.
    """
    from community_recovery import RecoveryDocker

    if not arguments or arguments[0] not in ("up", "build"):
        raise TrustError("community.trust.start-command-invalid")
    docker = RecoveryDocker(state)
    project = stack.project_name(state)
    if not docker.containers(project):
        return True
    if arguments[0] == "build":
        raise TrustError("community.trust.stop-before-build-required")
    if any(option in arguments for option in ("--build", "--force-recreate", "--always-recreate-deps", "--renew-anon-volumes", "-V")):
        raise TrustError("community.trust.stop-before-reprojection-required")
    identifiers = _healthy_containers(docker, project)
    initializers = []
    for identifier in identifiers:
        labels = json.loads(docker.text("inspect", "--format", "{{json .Config.Labels}}", identifier))
        if not isinstance(labels, dict) or labels.get("com.docker.compose.project") != project:
            raise TrustError("community.trust.runtime-invalid")
        if labels.get("com.docker.compose.service") == "initialize":
            initializers.append(labels)
    if len(initializers) != 1 or any(initializers[0].get(label) != values[variable] for label, variable in (
        ("io.iip.community.transport-generation", "IIP_COMMUNITY_TRANSPORT_GENERATION"),
        ("io.iip.community.installation-binding", "IIP_COMMUNITY_INSTALLATION_BINDING"),
        ("io.iip.community.deployment", "IIP_COMMUNITY_DEPLOYMENT"),
    )):
        raise TrustError("community.trust.stop-before-reprojection-required")
    return False


def record_started(state: Path) -> None:
    document = _document(state)
    if not document or not document["rotation"]:
        return
    from community_recovery import RecoveryDocker

    selected_transport(state, for_startup=True)
    docker = RecoveryDocker(state)
    containers = _healthy_containers(docker, stack.project_name(state))
    _atomic(state / STARTED, {"format": 1, "active": document["active"],
        "rotationId": document["rotation"]["id"], "phase": document["rotation"]["phase"],
        "binding": _binding(state), "containers": containers, "at": _stamp()})


def finalize(state: Path, *, new_trust_verified: bool) -> None:
    if not new_trust_verified:
        raise TrustError("community.trust.intake-verification-required")
    stack.require_complete_recovery(state)
    document = _document(state)
    if not document:
        raise TrustError("community.trust.rotation-required")
    if document["rotation"] is None and document["history"][-1]["action"] == "finalize":
        return
    if not document["rotation"] or document["rotation"]["phase"] not in ("activated", "rolled-back"):
        raise TrustError("community.trust.phase-invalid")
    selected_transport(state, for_startup=True)
    from community_recovery import RecoveryDocker

    docker = RecoveryDocker(state)
    receipt = stack.read_protected(state / STARTED)
    if (set(receipt) != {"format", "active", "rotationId", "phase", "binding", "containers", "at"}
            or type(receipt["format"]) is not int or receipt["format"] != 1
            or receipt["active"] != document["active"] or receipt["rotationId"] != document["rotation"]["id"]
            or receipt["phase"] != document["rotation"]["phase"] or receipt["binding"] != _binding(state)
            or not _date(receipt["at"]) or receipt["containers"] != _healthy_containers(docker, stack.project_name(state))):
        raise TrustError("community.trust.successful-start-required")
    stack.wait_for_collector(state)
    _event(document, "finalize")
    document["rotation"] = None
    _atomic(state / STATE_FILE, document)


def status(state: Path) -> dict:
    directory, identifier = selected_transport(state, require_current=False)
    summary = transport.validate_transport_directory(directory, require_current=False)
    document = _document(state)
    rotation = document["rotation"] if document else None
    now = datetime.now(timezone.utc)
    expiry = datetime.fromisoformat(summary["notAfter"])
    return {"activeGeneration": identifier, "caPath": str(directory / "ca.crt"),
        "overlapCaPath": str(state / OVERLAP / (rotation["id"] + ".crt")) if rotation else None,
        "phase": rotation["phase"] if rotation else "stable", **summary,
        "expired": not datetime.fromisoformat(summary["notBefore"]) <= now < expiry,
        "expiringSoon": expiry <= now + timedelta(days=30)}


def backup_paths(state: Path) -> list[str]:
    """Settled generations only; live startup receipts are not recovery input."""
    require_settled(state)
    document = _document(state)
    if document is None:
        return []
    selected_transport(state, require_current=False)
    result = [STATE_FILE]
    for root_name, limit in ((GENERATIONS, MAX_GENERATIONS), (OVERLAP, MAX_GENERATIONS)):
        root = state / root_name
        stack._private_directory(root)
        children = list(root.iterdir())
        if len(children) > limit:
            raise TrustError("community.trust.generation-limit")
        for path in sorted(children):
            if root_name == GENERATIONS:
                _generation(state, path.name, current=False)
                result.extend(f"{GENERATIONS}/{path.name}/{name}" for name in transport.TRANSPORT_FILES)
            else:
                if not re.fullmatch(r"[a-f0-9]{32}\.crt", path.name):
                    raise TrustError("community.trust.overlap-invalid")
                _read(path)
                result.append(f"{OVERLAP}/{path.name}")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=stack.DEFAULT_STATE)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "prepare", "activate", "rollback", "cancel", "finalize"):
        child = commands.add_parser(command)
        if command in ("activate", "rollback"):
            child.add_argument("--external-trust-staged", action="store_true")
        elif command == "finalize":
            child.add_argument("--new-trust-verified", action="store_true")
    arguments = parser.parse_args(argv)
    state = arguments.state.absolute()
    try:
        with stack.installation_lock(state):
            if arguments.command == "prepare":
                prepare(state)
            elif arguments.command in ("activate", "rollback"):
                switch(state, arguments.command, external_trust_staged=arguments.external_trust_staged)
            elif arguments.command == "finalize":
                finalize(state, new_trust_verified=arguments.new_trust_verified)
            elif arguments.command == "cancel":
                cancel(state)
            print(json.dumps(status(state), sort_keys=True))
        return 0
    except (ValueError, OSError, TypeError, KeyError, RecursionError):
        print("ERROR: community.trust.operation-failed; check phase, protected state, offline source, trust acknowledgements, certificate validity, and successful startup. No service was automatically started or stopped.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
