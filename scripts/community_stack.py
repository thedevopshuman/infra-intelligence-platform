#!/usr/bin/env python3
"""Protected, persistent single-host installation using existing IIP contracts."""

from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
import fcntl
import hashlib
import json
import os
import re
import secrets
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iip.adapters.otlp_ai_usage_receiver import ConfiguredAiUsageReceiver
from iip.application.attribute_ai_usage import validate_ai_attribution_policy
from iip.application.calculate_ai_cost import validate_ai_price_catalog
from iip.application.evaluate_ai_savings import validate_ai_savings_profile
from iip.application.qualify_ai_price_catalog import (
    verify_ai_price_catalog_qualification_report,
)

DEFAULT_STATE = ROOT / ".iip" / "community"
COMPOSE = ROOT / "deploy" / "docker-compose.community.yml"
CHANNEL = "bedrock-community"
SCOPE = "opentelemetry.instrumentation.botocore.bedrock-runtime"
INPUT_FILES = {
    "channel": "IIP_AI_USAGE_RECEIVER_CHANNELS_JSON",
    "catalogs": "IIP_AI_PRICE_CATALOGS_JSON",
    "attribution": "IIP_AI_ATTRIBUTION_POLICIES_JSON",
    "qualifications": "IIP_AI_PRICE_CATALOG_QUALIFICATIONS_JSON",
    "savings": "IIP_AI_SAVINGS_PROFILES_JSON",
}
USAGE_ATTRIBUTES = {
    "inputTokens": "gen_ai.usage.input_tokens",
    "outputTokens": "gen_ai.usage.output_tokens",
    "cacheReadInputTokens": "gen_ai.usage.cache_read.input_tokens",
    "cacheWriteInputTokens": "gen_ai.usage.cache_creation.input_tokens",
    "reasoningOutputTokens": "gen_ai.usage.reasoning.output_tokens",
    "zeroWhenAbsent": [],
    "reportedBy": "provider",
}
INVOCATION_ATTRIBUTES = {
    "attributes": {"requestId": "aws.request_id", "retryCount": "aws.retry_count"},
    "zeroWhenAbsent": [],
}


class InstallationError(ValueError):
    """Stable errors deliberately exclude protected configuration and tool output."""


def require_complete_recovery(state: Path) -> None:
    """An interrupted restore may only be inspected or stopped, never started."""
    marker = state / ".recovery-incomplete"
    if marker.exists() or marker.is_symlink():
        raise InstallationError("community.recovery.incomplete")


def compact(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise InstallationError("community.input.duplicate-key")
        result[key] = value
    return result


def read_protected(path: Path) -> dict:
    """Open the exact regular file, without following a final-component symlink."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "r", encoding="utf-8") as source:
            metadata = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
                or metadata.st_size > 16 * 1024 * 1024
            ):
                raise InstallationError("community.input.protection-required")
            result = json.load(source, object_pairs_hook=_object)
            if not isinstance(result, dict):
                raise InstallationError("community.input.invalid")
            return result
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InstallationError("community.input.unreadable") from error


def write_protected(path: Path, value: object) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(compact(value) + "\n")
        output.flush()
        os.fsync(output.fileno())


def _private_directory(path: Path) -> None:
    metadata = path.lstat()
    if (not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700
            or metadata.st_uid != os.getuid()):
        raise InstallationError("community.installation.protection-required")


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def installation_lock(state: Path):
    _private_directory(state)
    descriptor = os.open(state / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != os.getuid() or metadata.st_nlink != 1):
            raise InstallationError("community.installation.lock-invalid")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def _one(document: object, key: str) -> dict:
    if not isinstance(document, dict) or set(document) != {key}:
        raise InstallationError("community.input.wrapper-invalid")
    items = document[key]
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise InstallationError("community.input.single-tenant-required")
    return items[0]


def validate_inputs(inputs: Mapping[str, dict], receiver_token: str) -> tuple[dict, str]:
    """Revalidate existing contracts; never generate or approve prices/ownership."""
    try:
        if set(inputs) != set(INPUT_FILES):
            raise InstallationError("community.input.incomplete")
        normalized = json.loads(compact(inputs))
        channel = _one(normalized["channel"], "channels")
        channel["channelId"] = CHANNEL
        channel["tokenSha256"] = ConfiguredAiUsageReceiver.token_sha256(receiver_token)
        if (
            channel.get("provider") != "aws.bedrock"
            or channel.get("instrumentationScopes") != [SCOPE]
            or channel.get("usageAttributes") != USAGE_ATTRIBUTES
            or channel.get("invocationAttributes") != INVOCATION_ATTRIBUTES
        ):
            raise InstallationError("community.channel.unsupported-profile")
        ConfiguredAiUsageReceiver.from_json(compact(normalized["channel"]))
        tenant = channel["tenantId"]
        catalog_document = _one(normalized["catalogs"], "catalogs")
        catalog = validate_ai_price_catalog(catalog_document)
        policy = validate_ai_attribution_policy(_one(normalized["attribution"], "policies"))
        if catalog.tenant_id != tenant or policy.tenant_id != tenant:
            raise InstallationError("community.input.tenant-mismatch")
        if catalog.source_kind == "test-fixture" or policy.source_kind == "test-fixture":
            raise InstallationError("community.input.fixture-prohibited")
        qualifications = normalized["qualifications"]
        if set(qualifications) != {"policies", "reports"}:
            raise InstallationError("community.pricing.qualification-required")
        qualification_policy = _one({"policies": qualifications["policies"]}, "policies")
        qualification_report = _one({"reports": qualifications["reports"]}, "reports")
        report = verify_ai_price_catalog_qualification_report(
            qualification_report, catalog_document, qualification_policy,
            evaluated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        if (
            report["metadata"]["tenantId"] != tenant
            or report["spec"]["qualificationLevel"] != "production-catalog"
            or report["spec"]["status"] != "qualified"
        ):
            raise InstallationError("community.pricing.qualification-required")
        savings = normalized["savings"]
        if set(savings) != {"profiles"} or not isinstance(savings["profiles"], list):
            raise InstallationError("community.savings.invalid")
        if len(savings["profiles"]) > 100:
            raise InstallationError("community.savings.invalid")
        for document in savings["profiles"]:
            profile = validate_ai_savings_profile(document, allow_test_fixtures=False)
            if profile.tenant_id != tenant or profile.catalog_id != catalog.catalog_id:
                raise InstallationError("community.savings.source-mismatch")
        return normalized, tenant
    except InstallationError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        raise InstallationError("community.input.contract-invalid") from error


def project_name(state: Path) -> str:
    return "iip-community-" + hashlib.sha256(str(state).encode()).hexdigest()[:10]


def write_generation(state: Path, inputs: Mapping[str, dict]) -> str:
    from community_dashboard import write_dashboard
    from community_collector import write_collector

    parent = state / "config"
    parent.mkdir(mode=0o700, exist_ok=True)
    _private_directory(parent)
    with tempfile.TemporaryDirectory(prefix=".configuration-", dir=parent) as temporary:
        staged = Path(temporary) / "generation"
        staged.mkdir(mode=0o700)
        for name, document in inputs.items():
            write_protected(staged / f"{name}.json", document)
        pricing = inputs["catalogs"]["catalogs"][0]["spec"]
        write_dashboard(staged, pricing["currency"], pricing["currencyScale"])
        write_collector(staged, inputs["channel"])
        generation = generation_digest(staged, inputs)
        destination = parent / generation
        if destination.exists():
            _private_directory(destination)
            if generation_digest(destination, inputs) != generation or any(
                read_protected(destination / f"{name}.json") != inputs[name] for name in INPUT_FILES
            ):
                raise InstallationError("community.configuration.generation-invalid")
            return generation
        _sync_directory(staged)
        os.rename(staged, destination)
        _sync_directory(parent)
    return generation


def generation_digest(directory: Path, inputs: Mapping[str, dict]) -> str:
    dashboard = read_protected(directory / "dashboard.json")
    path = directory / "collector.yaml"
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_size > 4 * 1024 * 1024):
            raise InstallationError("community.configuration.generation-invalid")
        collector_hash = hashlib.sha256(source.read()).hexdigest()
    return hashlib.sha256(compact({"inputs": inputs, "dashboard": dashboard, "collector": collector_hash}).encode()).hexdigest()


def initialize(state: Path, inputs: Mapping[str, dict], *, image: str) -> Path:
    from community_transport import write_transport

    state = state.absolute()
    if state.exists() or state.is_symlink():
        raise InstallationError("community.installation.already-exists")
    try:
        relative = state.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        if len(relative.parts) < 2 or relative.parts[0] != ".iip":
            raise InstallationError("community.installation.build-context-prohibited")
    validate_image_selection(image)
    generated = {name: secrets.token_urlsafe(48) for name in (
        "apiToken", "collectorToken", "receiverToken", "databasePassword", "grafanaPassword",
    )}
    normalized, tenant = validate_inputs(inputs, generated["receiverToken"])
    state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Atomic directory promotion avoids half-installed state. Cleanup owns only
    # this newly allocated temporary directory, never an existing installation.
    with tempfile.TemporaryDirectory(prefix=".iip-install-", dir=state.parent) as temporary:
        staging = Path(temporary) / "installation"
        staging.mkdir(mode=0o700)
        transport_environment = write_transport(staging)
        generation = write_generation(staging, normalized)
        write_protected(staging / "installation.json", {
            "format": 1, "project": project_name(state), "image": image,
            "tenant": tenant, "transportEnvironment": transport_environment,
            "configurationGeneration": generation,
        })
        write_protected(staging / "credentials.json", generated)
        _sync_directory(staging)
        os.rename(staging, state)
        _sync_directory(state.parent)
    return state


def validate_image_selection(image: object) -> None:
    if not isinstance(image, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/@:-]{1,255}", image):
        raise InstallationError("community.image.invalid")
    if "@" in image:
        from community_images import require_digest_reference

        # A malformed digest selection must not fall back to tag/source mode.
        require_digest_reference(image)


def read_installation(state: Path) -> dict:
    """Validate non-secret installation metadata without loading credentials."""
    _private_directory(state)
    installation = read_protected(state / "installation.json")
    if (
        set(installation) != {"format", "project", "image", "tenant", "transportEnvironment", "configurationGeneration"}
        or type(installation["format"]) is not int or installation["format"] != 1
        or installation["project"] != project_name(state.absolute())
        or not isinstance(installation["configurationGeneration"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", installation["configurationGeneration"])
    ):
        raise InstallationError("community.installation.invalid")
    validate_image_selection(installation["image"])
    return installation


def prepare_images(state: Path, *, pull: bool = False) -> dict[str, str]:
    """Prepare the exact image cache; no installation secrets enter Docker."""
    from community_docker import local_docker_binding
    from community_images import selected_image_references, resolve_images

    state = state.absolute()
    require_complete_recovery(state)
    installation = read_installation(state)
    recovery = state / "recovery-images.json"
    if recovery.exists() or recovery.is_symlink():
        raise InstallationError("community.recovery.image-preparation-prohibited")
    selected_image_references(installation["image"])
    executable, endpoint, environment = local_docker_binding(state, dict(os.environ))
    return resolve_images(installation["image"], executable=executable,
        endpoint=endpoint, environment=environment, pull=pull)


def installed_environment(state: Path, *, validate_contracts: bool = True) -> tuple[dict[str, str], str]:
    state = state.absolute()
    _private_directory(state)
    if validate_contracts:
        require_complete_recovery(state)
    installation = read_installation(state)
    credentials = read_protected(state / "credentials.json")
    if (
        set(credentials) != {"apiToken", "collectorToken", "receiverToken", "databasePassword", "grafanaPassword"}
        or any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{64}", value)
               for value in credentials.values())
        or len(set(credentials.values())) != 5
        or not re.fullmatch(r"[a-f0-9]{64}", installation["configurationGeneration"])
    ):
        raise InstallationError("community.installation.invalid")
    generation = state / "config" / installation["configurationGeneration"]
    _private_directory(state / "config")
    _private_directory(generation)
    inputs = {name: read_protected(generation / f"{name}.json") for name in INPUT_FILES}
    if generation_digest(generation, inputs) != installation["configurationGeneration"]:
        raise InstallationError("community.configuration.generation-invalid")
    tenant = installation["tenant"]
    if validate_contracts:
        inputs, tenant = validate_inputs(inputs, credentials["receiverToken"])
        if tenant != installation["tenant"]:
            raise InstallationError("community.installation.tenant-change-prohibited")
    environment = {
        "IIP_COMMUNITY_STATE": str(state),
        "IIP_COMMUNITY_DASHBOARD": str(generation / "dashboard.json"),
        "IIP_COMMUNITY_COLLECTOR_CONFIG": str(generation / "collector.yaml"),
        "IIP_COMMUNITY_IMAGE": installation["image"],
        "IIP_COMMUNITY_TENANT_ID": tenant,
        "IIP_COMMUNITY_POSTGRES_PASSWORD": credentials["databasePassword"],
        "IIP_COMMUNITY_COLLECTOR_TOKEN": credentials["collectorToken"],
        "IIP_COMMUNITY_RECEIVER_TOKEN": credentials["receiverToken"],
        "IIP_COMMUNITY_GRAFANA_PASSWORD": credentials["grafanaPassword"],
        "IIP_DATABASE_URL": f"postgresql://iip:{credentials['databasePassword']}@postgres:5432/iip",
        "IIP_AUTH_IDENTITIES_JSON": compact({"identities": [{
            "tokenSha256": ConfiguredAiUsageReceiver.token_sha256(credentials["apiToken"]),
            "actorId": "community-operator", "tenantId": tenant,
            "roles": ["developer", "platform-admin"],
        }]}),
        "IIP_AI_SAVINGS_ENGINE_ENABLED": "true" if inputs["savings"]["profiles"] else "false",
        **{variable: compact(inputs[name]) for name, variable in INPUT_FILES.items()},
    }
    from community_transport import transport_environment

    # A modified manifest cannot silently downgrade the packaged TLS posture.
    transport = installation["transportEnvironment"]
    if transport != transport_environment():
        raise InstallationError("community.transport.invalid")
    if validate_contracts:
        from community_trust import selected_transport

        directory, transport_generation = selected_transport(state, for_startup=True)
    else:
        # Shutdown/status do not consume mounted material. Keep rescue possible
        # even when a certificate or lifecycle document needs repair.
        directory, transport_generation = state / "transport", "0" * 64
    environment["IIP_COMMUNITY_TRANSPORT_DIRECTORY"] = str(directory)
    environment["IIP_COMMUNITY_TRANSPORT_GENERATION"] = transport_generation
    environment.update(transport)
    if validate_contracts:
        from community_recovery import deployment_digest, recovered_environment

        # Restored startup must preserve the images that actually held these
        # volumes, not re-resolve the original mutable tags. Rescue ps/down
        # intentionally skip this deployment check and cannot create services.
        environment.update(recovered_environment(state))
        environment["IIP_COMMUNITY_DEPLOYMENT"] = deployment_digest()
    else:
        environment["IIP_COMMUNITY_DEPLOYMENT"] = "0" * 64
    environment["IIP_COMMUNITY_INSTALLATION_BINDING"] = hashlib.sha256(
        compact({"installation": installation, "credentials": credentials}).encode()
    ).hexdigest()
    return environment, installation["project"]


def run_compose(state: Path, arguments: Sequence[str], *, validate_contracts: bool = True) -> str:
    from community_docker import CommunityDockerError, local_docker_binding
    from community_images import CommunityImageError, resolve_images

    values, project = installed_environment(state, validate_contracts=validate_contracts)
    arguments = list(arguments)
    digest_selected = "@" in values["IIP_COMMUNITY_IMAGE"]
    if digest_selected and (arguments[0] == "build" or "--build" in arguments):
        raise InstallationError("community.image.digest-build-prohibited")
    try:
        executable, endpoint, environment = local_docker_binding(state, dict(os.environ))
        if arguments[0] in ("up", "build"):
            if not validate_contracts:
                raise InstallationError("community.start.validation-required")
            from community_trust import guard_start

            if not guard_start(state, arguments, values):
                return ""  # Already healthy: never rerun a projector against live files.
        if arguments[0] == "up" and digest_selected:
            # Recovery has already replaced its selection with recorded local
            # IDs; it must never enter the registry-reference resolver here.
            values.update(resolve_images(values["IIP_COMMUNITY_IMAGE"],
                executable=executable, endpoint=endpoint, environment=dict(environment)))
            if "--no-build" not in arguments:
                arguments.append("--no-build")
            if "--pull" in arguments:
                offset = arguments.index("--pull")
                if arguments[offset + 1:offset + 2] != ["never"]:
                    raise InstallationError("community.image.implicit-pull-prohibited")
            else:
                arguments.extend(("--pull", "never"))
        environment.update(values)
        completed = subprocess.run(
            [executable, "--host", endpoint, "compose", "--env-file", os.devnull,
             "--project-name", project, "--file", str(COMPOSE), *arguments],
            cwd=ROOT, env=environment, capture_output=True, text=True, check=True,
            timeout=600 if arguments[0] == "build" else 300,
        )
    except (CommunityDockerError, CommunityImageError) as error:
        raise InstallationError(str(error)) from None
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise InstallationError("community.docker.command-failed") from error
    return completed.stdout


def configure(state: Path, inputs: Mapping[str, dict]) -> None:
    """Switch an offline installation to a complete immutable configuration set."""
    state = state.absolute()
    require_complete_recovery(state)
    from community_trust import require_settled

    require_settled(state)
    if run_compose(state, ["ps", "--all", "--quiet"], validate_contracts=False).strip():
        raise InstallationError("community.configuration.stop-required")
    installation = read_protected(state / "installation.json")
    credentials = read_protected(state / "credentials.json")
    normalized, tenant = validate_inputs(inputs, credentials["receiverToken"])
    if tenant != installation["tenant"]:
        raise InstallationError("community.installation.tenant-change-prohibited")
    installation["configurationGeneration"] = write_generation(state, normalized)
    descriptor, path = tempfile.mkstemp(prefix=".installation-", dir=state)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(compact(installation) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(path, state / "installation.json")
        _sync_directory(state)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def wait_for_collector(state: Path, *, timeout_seconds: int = 60) -> None:
    """Prove the selected TLS listener rejects unauthenticated empty exports."""
    from community_trust import selected_transport

    directory, _ = selected_transport(state, for_startup=True)
    context = ssl.create_default_context(cafile=str(directory / "ca.crt"))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        request = urllib.request.Request("https://localhost:14322/v1/traces", data=b"", headers={"Content-Type": "application/x-protobuf"})
        try:
            with opener.open(request, timeout=2):
                raise InstallationError("community.collector.authentication-required")
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                error.close()
                return
            error.close()
            raise InstallationError("community.collector.unexpected-response") from None
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)
    raise InstallationError("community.collector.unavailable")


def execute(arguments: argparse.Namespace) -> int:
    if arguments.command in ("init", "configure"):
        inputs = {name: read_protected(getattr(arguments, name))
                  if getattr(arguments, name) is not None else {"profiles": []}
                  for name in INPUT_FILES}
        if arguments.command == "init":
            initialize(arguments.state, inputs, image=arguments.image)
            print("Protected installation prepared. No containers started; no provider was called.")
        else:
            configure(arguments.state, inputs)
            print("Stopped installation reconfigured. Prior snapshots, credentials, and data are retained.")
    elif arguments.command == "check":
        installed_environment(arguments.state)
        print("Protected configuration is valid; live telemetry and release readiness are not certified.")
    elif arguments.command == "images":
        prepare_images(arguments.state, pull=arguments.pull)
        print("All five selected image digests are present for this daemon's Linux platform.")
        print("Publisher/signature verification is separate. No containers were started or changed.")
    elif arguments.command == "up":
        from community_recovery import RecoveryDocker, record_runtime

        environment, _ = installed_environment(arguments.state)
        recovered = "IIP_COMMUNITY_POSTGRES_IMAGE" in environment
        if arguments.build and not recovered and "@" in environment["IIP_COMMUNITY_IMAGE"]:
            raise InstallationError("community.image.digest-build-prohibited")
        # A missing selected image must never trigger an implicit source build.
        # The explicit --build path below builds once before starting services.
        options = ["up", "--detach", "--wait", "--wait-timeout", "240", "--no-build"]
        if recovered:
            if arguments.build:
                raise InstallationError("community.recovery.build-prohibited")
            RecoveryDocker(arguments.state).require_images(
                read_protected(arguments.state / "recovery-images.json")
            )
            options.extend(("--pull", "never"))
        if arguments.build:
            run_compose(arguments.state, ["build", "initialize"])
        run_compose(arguments.state, options)
        wait_for_collector(arguments.state)
        record_runtime(arguments.state)
        from community_trust import record_started

        record_started(arguments.state)
        print("Persistent single-host preview started without demo data.")
        print("Console: http://127.0.0.1:18083/console")
        print("Grafana: http://127.0.0.1:13001/d/iip-community-ai-finops (user: admin)")
        print("OTLP traces: https://localhost:14322/v1/traces")
        print("Credentials are in the protected installation directory; they are never printed.")
    elif arguments.command == "status":
        print(run_compose(arguments.state, ["ps"], validate_contracts=False), end="")
    else:
        run_compose(arguments.state, ["down"], validate_contracts=False)
        print("Stopped. Database, Collector queue, Prometheus, Grafana, and credentials are retained.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    subparsers = parser.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init", help="validate operator inputs and generate protected credentials")
    config = subparsers.add_parser("configure", help="replace a stopped installation's protected configuration")
    for selected in (init, config):
        for name in INPUT_FILES:
            selected.add_argument(f"--{name}", type=Path, required=name != "savings")
    init.add_argument("--image", default="iip-community:0.84.0")
    subparsers.add_parser("check", help="revalidate protected inputs without Docker or provider calls")
    images = subparsers.add_parser("images", help="check exact selected image digests in the local daemon; not publisher verification")
    images.add_argument("--pull", action="store_true", help="explicitly download the five selected image digests without installation credentials")
    up = subparsers.add_parser("up", help="start without adding demo data")
    up.add_argument("--build", action="store_true", help="build the application image from this checkout")
    subparsers.add_parser("status", help="show Compose health without printing credentials")
    subparsers.add_parser("down", help="stop containers; keep all named data volumes and credentials")
    arguments = parser.parse_args(argv)
    try:
        with nullcontext() if arguments.command == "init" else installation_lock(arguments.state):
            return execute(arguments)
    except (InstallationError, OSError, ValueError, TypeError, KeyError, RecursionError):
        # Input/parser/provider details may contain sensitive material.
        print("ERROR: community.operation.failed; check protected inputs, permissions, certificate/report validity, and Docker availability. If containers already exist, inspect status; stop before rebuilding or repairing.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
