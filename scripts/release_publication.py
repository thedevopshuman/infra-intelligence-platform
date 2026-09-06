#!/usr/bin/env python3
"""Publish exact verified release indexes and retain unsigned evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Mapping, Protocol, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_bundle  # noqa: E402
import release_signature_verification  # noqa: E402
import validate_schemas  # noqa: E402


REPORT_SCHEMA = (
    ROOT / "contracts" / "schemas" / "release-publication-report.schema.json"
)
ROLES = ("control-plane-image", "plugin-mediation-bridge-image")
CHECK_IDS = (
    "release-bundle-integrity",
    "source-binding",
    "exact-version-tag",
    "distinct-role-repositories",
    "control-plane-index-copy",
    "bridge-index-copy",
    "registry-digest-verification",
    "output-minimization",
)
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
RELEASE_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
REPOSITORY = re.compile(
    r"^[a-z0-9][a-z0-9.-]*(?::[0-9]{1,5})?/"
    r"[a-z0-9]+(?:[._/-][a-z0-9]+)*$"
)
GITHUB_REPOSITORY = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$"
)
SOURCE_REVISION = re.compile(r"^[a-f0-9]{40,64}$")
BUILDX_VERSION = re.compile(
    r"\bv?([0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?)\b"
)
MAX_COMMAND_OUTPUT_BYTES = 2_097_152
MAX_REPORT_BYTES = 2_097_152
MAX_ARCHIVE_MEMBERS = 100_000
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024 * 1024
COSIGN_VERSION = "3.1.2"


class ReleasePublicationError(RuntimeError):
    """A stable release-publication failure."""


class IndexPublisher(Protocol):
    def version(self) -> str:
        """Return the normalized publication tool version."""

    def publish(
        self,
        *,
        archive: Path,
        repository: str,
        tag: str,
        digest: str,
    ) -> None:
        """Copy and verify one exact OCI index."""


class DockerBuildxPublisher:
    """Copy OCI layouts by digest with Docker Buildx imagetools."""

    def __init__(self, executable: str = "docker") -> None:
        if (
            not executable
            or len(executable) > 2048
            or any(character.isspace() or ord(character) < 32 for character in executable)
        ):
            raise ReleasePublicationError("release-publication.tool.invalid")
        self._executable = executable

    def version(self) -> str:
        completed = self._run((self._executable, "buildx", "version"), timeout=30)
        if completed.returncode != 0:
            raise ReleasePublicationError("release-publication.tool.unavailable")
        match = BUILDX_VERSION.search(completed.stdout)
        if match is None or SEMVER.fullmatch(match.group(1)) is None:
            raise ReleasePublicationError("release-publication.tool.output-invalid")
        return match.group(1)

    def publish(
        self,
        *,
        archive: Path,
        repository: str,
        tag: str,
        digest: str,
    ) -> None:
        tag_reference = f"{repository}:{tag}"
        current = self._inspect(tag_reference, optional=True)
        if current is not None:
            if current != digest:
                raise ReleasePublicationError("release-publication.tag.conflict")
            if self._inspect(f"{repository}@{digest}") != digest:
                raise ReleasePublicationError("release-publication.digest.mismatch")
            return

        with tempfile.TemporaryDirectory(prefix="iip-release-layout-") as temporary:
            layout = Path(temporary)
            _extract_oci_layout(archive, layout)
            source = f"oci-layout://{layout.resolve()}@{digest}"
            completed = self._run(
                (
                    self._executable,
                    "buildx",
                    "imagetools",
                    "create",
                    "--progress",
                    "plain",
                    "--tag",
                    tag_reference,
                    source,
                ),
                timeout=900,
            )
            if completed.returncode != 0:
                raise ReleasePublicationError("release-publication.copy.failed")

        if (
            self._inspect(f"{repository}@{digest}") != digest
            or self._inspect(tag_reference) != digest
        ):
            raise ReleasePublicationError("release-publication.digest.mismatch")

    def _inspect(self, reference: str, *, optional: bool = False) -> str | None:
        completed = self._run(
            (
                self._executable,
                "buildx",
                "imagetools",
                "inspect",
                reference,
                "--format",
                "{{json .Manifest}}",
            ),
            timeout=180,
        )
        if completed.returncode != 0:
            if optional:
                return None
            raise ReleasePublicationError("release-publication.inspect.failed")
        try:
            document = json.loads(completed.stdout)
        except (UnicodeError, json.JSONDecodeError):
            raise ReleasePublicationError(
                "release-publication.tool.output-invalid"
            ) from None
        digest = document.get("digest") if isinstance(document, dict) else None
        if not isinstance(digest, str) or DIGEST.fullmatch(digest) is None:
            raise ReleasePublicationError("release-publication.tool.output-invalid")
        return digest

    @staticmethod
    def _run(
        command: Sequence[str], *, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        try:
            completed = subprocess.run(
                tuple(command),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                check=False,
                env=os.environ.copy(),
            )
        except (OSError, subprocess.SubprocessError):
            raise ReleasePublicationError(
                "release-publication.tool.unavailable"
            ) from None
        if (
            len(completed.stdout.encode("utf-8", errors="replace"))
            > MAX_COMMAND_OUTPUT_BYTES
            or len(completed.stderr.encode("utf-8", errors="replace"))
            > MAX_COMMAND_OUTPUT_BYTES
        ):
            raise ReleasePublicationError("release-publication.tool.output-invalid")
        return completed


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def report_id(report: Mapping[str, object]) -> str:
    metadata = report.get("metadata")
    spec = report.get("spec")
    if not isinstance(metadata, Mapping) or not isinstance(spec, Mapping):
        raise ReleasePublicationError("release-publication.report.invalid")
    identity = {
        "sourceRevision": metadata.get("sourceRevision"),
        "sourceDirty": metadata.get("sourceDirty"),
        "release": spec.get("release"),
        "channel": spec.get("channel"),
        "environment": spec.get("environment"),
        "targets": spec.get("targets"),
        "checks": spec.get("checks"),
    }
    return "rpr_" + hashlib.sha256(canonical_bytes(identity)).hexdigest()[:32]


def _repository_host(repository: str) -> str:
    if REPOSITORY.fullmatch(repository) is None or len(repository) > 512:
        raise ReleasePublicationError("release-publication.repository.invalid")
    host = repository.split("/", 1)[0]
    if ":" in host:
        port_text = host.rsplit(":", 1)[1]
        if not port_text.isdigit() or not 1 <= int(port_text) <= 65_535:
            raise ReleasePublicationError("release-publication.repository.invalid")
    return host


def _target(
    role: str,
    repository: str,
    tag: str,
    digest: str,
) -> dict[str, str]:
    return {
        "role": role,
        "repository": repository,
        "tagReference": f"{repository}:{tag}",
        "immutableReference": f"{repository}@{digest}",
        "indexDigest": digest,
    }


def _validate_repositories(repositories: Mapping[str, str]) -> str:
    if tuple(repositories) != ROLES:
        raise ReleasePublicationError("release-publication.targets.invalid")
    hosts = tuple(_repository_host(repositories[role]) for role in ROLES)
    if hosts[0] != hosts[1] or len(set(repositories.values())) != len(ROLES):
        raise ReleasePublicationError("release-publication.repositories.invalid")
    return hosts[0]


def build_report(
    *,
    version: str,
    tag: str,
    manifest_digest: str,
    source_revision: str,
    source_dirty: bool,
    repositories: Mapping[str, str],
    index_digests: Mapping[str, str],
    buildx_version: str,
    generated_at: str,
    platform_name: str,
    python_version: str,
) -> dict[str, object]:
    if tuple(index_digests) != ROLES:
        raise ReleasePublicationError("release-publication.targets.invalid")
    registry_host = _validate_repositories(repositories)
    if (
        RELEASE_VERSION.fullmatch(version) is None
        or tag != f"v{version}"
        or DIGEST.fullmatch(manifest_digest) is None
        or any(DIGEST.fullmatch(index_digests[role]) is None for role in ROLES)
        or SEMVER.fullmatch(buildx_version) is None
    ):
        raise ReleasePublicationError("release-publication.identity.invalid")

    checks = [{"id": identifier, "status": "passed"} for identifier in CHECK_IDS]
    report: dict[str, object] = {
        "apiVersion": "iip.dev/v1alpha1",
        "kind": "ReleasePublicationReport",
        "metadata": {
            "id": "rpr_" + "0" * 32,
            "generatedAt": generated_at,
            "sourceRevision": source_revision,
            "sourceDirty": source_dirty,
        },
        "spec": {
            "status": "published-unsigned",
            "promotionStatus": (
                "requires-signature-and-vulnerability-qualification"
            ),
            "release": {
                "version": version,
                "tag": tag,
                "manifestDigest": manifest_digest,
            },
            "channel": {
                "profile": "oci-layout-to-registry-v1",
                "registryHost": registry_host,
            },
            "environment": {
                "platform": platform_name,
                "pythonVersion": python_version,
                "dockerBuildxVersion": buildx_version,
            },
            "targets": [
                _target(
                    role,
                    repositories[role],
                    tag,
                    index_digests[role],
                )
                for role in ROLES
            ],
            "checks": checks,
            "summary": {
                "totalChecks": len(checks),
                "passedChecks": len(checks),
                "failedChecks": 0,
                "overallStatus": "published-unsigned",
            },
        },
    }
    report["metadata"]["id"] = report_id(report)  # type: ignore[index]
    validate_report(report)
    return report


def validate_report(report: object) -> None:
    schema = _read_json(REPORT_SCHEMA, "release-publication.schema.invalid")
    errors = validate_schemas.instance_validation_errors(
        schema,
        report,
        label="release publication report",
    )
    if errors or not isinstance(report, dict):
        raise ReleasePublicationError("release-publication.report.invalid")
    metadata = report["metadata"]
    spec = report["spec"]
    assert isinstance(metadata, dict) and isinstance(spec, dict)
    release = spec["release"]
    channel = spec["channel"]
    targets = spec["targets"]
    checks = spec["checks"]
    assert (
        isinstance(release, dict)
        and isinstance(channel, dict)
        and isinstance(targets, list)
        and isinstance(checks, list)
    )
    if (
        release["tag"] != f"v{release['version']}"
        or tuple(item.get("role") for item in targets) != ROLES
        or tuple(item.get("id") for item in checks) != CHECK_IDS
        or len({item.get("repository") for item in targets}) != len(ROLES)
    ):
        raise ReleasePublicationError("release-publication.report.invalid")
    for target in targets:
        assert isinstance(target, dict)
        repository = target["repository"]
        digest = target["indexDigest"]
        if (
            target["tagReference"] != f"{repository}:{release['tag']}"
            or target["immutableReference"] != f"{repository}@{digest}"
            or _repository_host(repository) != channel["registryHost"]
        ):
            raise ReleasePublicationError("release-publication.report.invalid")
    if metadata["id"] != report_id(report):
        raise ReleasePublicationError("release-publication.report.invalid")


def source_identity() -> tuple[str, bool]:
    try:
        revision = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ("git", "status", "--porcelain", "--untracked-files=normal"),
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=10,
                check=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        raise ReleasePublicationError(
            "release-publication.source.unavailable"
        ) from None
    return revision, dirty


def project_version() -> str:
    try:
        document = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        version = document["project"]["version"]
    except (OSError, UnicodeError, KeyError, TypeError, tomllib.TOMLDecodeError):
        raise ReleasePublicationError(
            "release-publication.version.invalid"
        ) from None
    if not isinstance(version, str) or RELEASE_VERSION.fullmatch(version) is None:
        raise ReleasePublicationError("release-publication.version.invalid")
    return version


def validate_github_release_context(
    *,
    tag: str,
    revision: str,
    main_revision: str,
    github_repository: str,
) -> None:
    """Fail closed unless a clean exact-version tag names the current main tip."""

    if GITHUB_REPOSITORY.fullmatch(github_repository) is None:
        raise ReleasePublicationError("release-publication.github.invalid")
    if (
        SOURCE_REVISION.fullmatch(revision) is None
        or SOURCE_REVISION.fullmatch(main_revision) is None
    ):
        raise ReleasePublicationError("release-publication.source.invalid")
    if tag != f"v{project_version()}":
        raise ReleasePublicationError("release-publication.tag.invalid")
    current_revision, dirty = source_identity()
    if current_revision != revision or revision != main_revision:
        raise ReleasePublicationError("release-publication.source.mismatch")
    if dirty:
        raise ReleasePublicationError("release-publication.source.dirty")


def publish_candidate(
    *,
    bundle: Path,
    repositories: Mapping[str, str],
    tag: str,
    output: Path,
    publisher: IndexPublisher,
) -> dict[str, object]:
    selected_output = output.expanduser().absolute()
    if selected_output.exists() or selected_output.is_symlink():
        raise ReleasePublicationError("release-publication.output.exists")
    selected_bundle = bundle.expanduser().resolve()
    try:
        manifest = release_bundle.verify_bundle(selected_bundle)
    except release_bundle.ReleaseBundleError:
        raise ReleasePublicationError("release-publication.bundle.invalid") from None
    metadata = manifest.get("metadata")
    spec = manifest.get("spec")
    if not isinstance(metadata, dict) or not isinstance(spec, dict):
        raise ReleasePublicationError("release-publication.bundle.invalid")
    revision, dirty = source_identity()
    if metadata.get("revision") != revision:
        raise ReleasePublicationError("release-publication.source.mismatch")
    version = metadata.get("version")
    if not isinstance(version, str) or tag != f"v{version}":
        raise ReleasePublicationError("release-publication.tag.invalid")
    _validate_repositories(repositories)

    images = (spec.get("image"), spec.get("pluginMediationBridgeImage"))
    index_digests: dict[str, str] = {}
    for role, image in zip(ROLES, images):
        if not isinstance(image, dict):
            raise ReleasePublicationError("release-publication.bundle.invalid")
        filename = image.get("path")
        digest = image.get("indexDigest")
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not isinstance(digest, str)
            or DIGEST.fullmatch(digest) is None
        ):
            raise ReleasePublicationError("release-publication.bundle.invalid")
        archive = selected_bundle / filename
        publisher.publish(
            archive=archive,
            repository=repositories[role],
            tag=tag,
            digest=digest,
        )
        index_digests[role] = digest

    try:
        manifest_bytes = (selected_bundle / "release-manifest.json").read_bytes()
    except OSError:
        raise ReleasePublicationError("release-publication.bundle.invalid") from None
    report = build_report(
        version=version,
        tag=tag,
        manifest_digest="sha256:" + hashlib.sha256(manifest_bytes).hexdigest(),
        source_revision=revision,
        source_dirty=dirty,
        repositories=repositories,
        index_digests=index_digests,
        buildx_version=publisher.version(),
        generated_at=_timestamp(),
        platform_name=f"{platform.system().lower()}/{platform.machine() or 'unknown'}",
        python_version=platform.python_version(),
    )
    _write_json(selected_output, report, "release-publication.output.invalid")
    return report


def github_signature_policy(
    report: Mapping[str, object],
    *,
    github_repository: str,
    generation: int,
    effective_at: str,
) -> dict[str, object]:
    validate_report(report)
    if GITHUB_REPOSITORY.fullmatch(github_repository) is None:
        raise ReleasePublicationError("release-publication.github.invalid")
    if not 1 <= generation <= 2_147_483_647:
        raise ReleasePublicationError("release-publication.github.invalid")
    spec = report["spec"]
    assert isinstance(spec, Mapping)
    release = spec["release"]
    targets = spec["targets"]
    assert isinstance(release, Mapping) and isinstance(targets, list)
    tag = release["tag"]
    identity = (
        f"https://github.com/{github_repository}/.github/workflows/"
        f"release.yml@refs/tags/{tag}"
    )
    policy: dict[str, object] = {
        "apiVersion": "iip.dev/v1alpha1",
        "kind": "ReleaseSignaturePolicy",
        "metadata": {
            "id": f"rsp_github-release-{tag}",
            "generation": generation,
            "effectiveAt": effective_at,
        },
        "spec": {
            "profile": "sigstore-keyless-v1",
            "cosignVersion": COSIGN_VERSION,
            "transparencyMode": "required",
            "artifacts": [
                {
                    "role": role,
                    "repository": target["repository"],
                    "trust": {
                        "mode": "keyless",
                        "identities": [
                            {
                                "id": "github-release-workflow",
                                "certificateIdentity": identity,
                                "certificateOidcIssuer": (
                                    "https://token.actions.githubusercontent.com"
                                ),
                            }
                        ],
                    },
                }
                for role, target in zip(ROLES, targets)
            ],
        },
    }
    try:
        release_signature_verification.validate_policy_document(
            policy,
            promotion=True,
        )
    except release_signature_verification.ReleaseSignatureError:
        raise ReleasePublicationError(
            "release-publication.github.policy-invalid"
        ) from None
    return policy


def report_reference(report: Mapping[str, object], role: str) -> str:
    validate_report(report)
    if role not in ROLES:
        raise ReleasePublicationError("release-publication.role.invalid")
    spec = report["spec"]
    assert isinstance(spec, Mapping)
    targets = spec["targets"]
    assert isinstance(targets, list)
    target = targets[ROLES.index(role)]
    assert isinstance(target, Mapping)
    return str(target["immutableReference"])


def _extract_oci_layout(archive_path: Path, destination: Path) -> None:
    try:
        archive = tarfile.open(archive_path, mode="r:*")
    except (OSError, tarfile.TarError):
        raise ReleasePublicationError("release-publication.archive.invalid") from None
    with archive:
        members = archive.getmembers()
        if not members or len(members) > MAX_ARCHIVE_MEMBERS:
            raise ReleasePublicationError("release-publication.archive.invalid")
        total = 0
        seen: set[tuple[str, ...]] = set()
        for member in members:
            relative = PurePosixPath(member.name)
            parts = relative.parts
            if (
                relative.is_absolute()
                or not parts
                or any(part in ("", ".", "..") for part in parts)
                or parts in seen
                or not (member.isfile() or member.isdir())
                or member.size < 0
            ):
                raise ReleasePublicationError("release-publication.archive.invalid")
            seen.add(parts)
            total += member.size
            if total > MAX_ARCHIVE_BYTES:
                raise ReleasePublicationError("release-publication.archive.invalid")
            target = destination.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ReleasePublicationError("release-publication.archive.invalid")
            _copy_member(source, target)
    if not (destination / "index.json").is_file() or not (
        destination / "oci-layout"
    ).is_file():
        raise ReleasePublicationError("release-publication.archive.invalid")


def _copy_member(source: BinaryIO, target: Path) -> None:
    try:
        with target.open("xb") as destination:
            shutil.copyfileobj(source, destination, length=1024 * 1024)
    except OSError:
        raise ReleasePublicationError("release-publication.archive.invalid") from None


def _read_json(path: Path, code: str) -> dict[str, object]:
    try:
        if path.stat().st_size > MAX_REPORT_BYTES:
            raise OSError
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ReleasePublicationError(code) from None
    if not isinstance(document, dict):
        raise ReleasePublicationError(code)
    return document


def _write_json(path: Path, value: object, code: str) -> None:
    destination = path.expanduser().absolute()
    if destination.is_symlink():
        raise ReleasePublicationError(code)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            temporary = Path(handle.name)
        os.chmod(temporary, 0o644)
        os.link(temporary, destination)
    except OSError:
        raise ReleasePublicationError(code) from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _repositories(arguments: argparse.Namespace) -> dict[str, str]:
    return {
        ROLES[0]: arguments.control_plane_repository,
        ROLES[1]: arguments.bridge_repository,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    publish = subparsers.add_parser("publish")
    publish.add_argument("bundle", type=Path)
    publish.add_argument("--control-plane-repository", required=True)
    publish.add_argument("--bridge-repository", required=True)
    publish.add_argument("--tag", required=True)
    publish.add_argument("--report", type=Path, required=True)
    publish.add_argument("--docker", default="docker")

    reference = subparsers.add_parser("reference")
    reference.add_argument("report", type=Path)
    reference.add_argument("--role", choices=ROLES, required=True)

    policy = subparsers.add_parser("github-signature-policy")
    policy.add_argument("report", type=Path)
    policy.add_argument("--github-repository", required=True)
    policy.add_argument("--generation", type=int, required=True)
    policy.add_argument("--effective-at")
    policy.add_argument("--output", type=Path, required=True)

    context = subparsers.add_parser("validate-github-context")
    context.add_argument("--tag", required=True)
    context.add_argument("--revision", required=True)
    context.add_argument("--main-revision", required=True)
    context.add_argument("--github-repository", required=True)
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    try:
        if arguments.command == "publish":
            report = publish_candidate(
                bundle=arguments.bundle,
                repositories=_repositories(arguments),
                tag=arguments.tag,
                output=arguments.report,
                publisher=DockerBuildxPublisher(arguments.docker),
            )
            print(
                "release indexes published unchanged: "
                f"{report['metadata']['id']}"  # type: ignore[index]
            )
        elif arguments.command == "reference":
            report = _read_json(
                arguments.report.expanduser().resolve(),
                "release-publication.report.invalid",
            )
            print(report_reference(report, arguments.role))
        elif arguments.command == "github-signature-policy":
            report = _read_json(
                arguments.report.expanduser().resolve(),
                "release-publication.report.invalid",
            )
            policy = github_signature_policy(
                report,
                github_repository=arguments.github_repository,
                generation=arguments.generation,
                effective_at=arguments.effective_at or _timestamp(),
            )
            _write_json(
                arguments.output,
                policy,
                "release-publication.github.policy-output-invalid",
            )
            print(f"GitHub release signature policy written: {arguments.output}")
        else:
            validate_github_release_context(
                tag=arguments.tag,
                revision=arguments.revision,
                main_revision=arguments.main_revision,
                github_repository=arguments.github_repository,
            )
            print("GitHub release context is exact, clean, and main-bound")
    except ReleasePublicationError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
