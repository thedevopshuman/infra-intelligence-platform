from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from scripts import recover_source_assets as recovery


ROOT = Path(__file__).resolve().parents[1]


def run(*arguments: str, cwd: Path) -> str:
    return subprocess.run(
        arguments,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
        check=True,
    ).stdout.strip()


class RecoverSourceAssetsTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="iip-source-recovery-test-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.source = self.directory / "source"
        run(
            "git",
            "clone",
            "--quiet",
            "--shared",
            str(ROOT),
            str(self.source),
            cwd=self.directory,
        )
        run("git", "checkout", "--quiet", "--detach", recovery.RELEASE_TAG, cwd=self.source)
        self.output = self.directory / "recovered-assets"
        self.tool_log = self.directory / "tools.jsonl"

    def tool(self, name: str, *, extra: bool = False) -> str:
        path = self.directory / name
        body = f"""#!{sys.executable}
import io, json, os, pathlib, sys, tarfile
log = pathlib.Path({str(self.tool_log)!r})
record = {{
    "tool": pathlib.Path(sys.argv[0]).name,
    "arguments": sys.argv[1:],
    "cwd": os.getcwd(),
    "npmToken": "NPM_TOKEN" in os.environ,
    "nodeOptions": "NODE_OPTIONS" in os.environ,
    "pythonPath": "PYTHONPATH" in os.environ,
    "userconfig": os.environ.get("NPM_CONFIG_USERCONFIG"),
    "globalconfig": os.environ.get("NPM_CONFIG_GLOBALCONFIG"),
    "cache": os.environ.get("NPM_CONFIG_CACHE"),
    "packageLock": (pathlib.Path.cwd() / "package-lock.json").is_file(),
}}
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, sort_keys=True) + "\\n")
if pathlib.Path(sys.argv[0]).name.startswith("fake-npm") and len(sys.argv) > 1 and sys.argv[1] == "pack":
    destination = pathlib.Path(sys.argv[sys.argv.index("--pack-destination") + 1])
    with tarfile.open(destination / "iip-sdk-0.84.0.tgz", mode="w:gz") as archive:
        content = b"typescript package"
        member = tarfile.TarInfo("package/dist/index.js")
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
        private = pathlib.Path.cwd() / "dist/private.json"
        if private.is_file():
            private_content = private.read_bytes()
            private_member = tarfile.TarInfo("package/dist/private.json")
            private_member.size = len(private_content)
            archive.addfile(private_member, io.BytesIO(private_content))
    if {extra!r}:
        (destination / "unexpected-operator-file").write_bytes(b"private sentinel")
"""
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
        return str(path)

    def build(self, *, extra: bool = False) -> dict[str, object]:
        npm = self.tool("fake-npm-extra" if extra else "fake-npm", extra=extra)
        helm = self.tool("fake-helm")
        with patch.dict(
            os.environ,
            {
                "NPM_TOKEN": "must-not-reach-tool",
                "NODE_OPTIONS": "--require=/private/injected.js",
                "PYTHONPATH": "/private/injected-python",
                "NPM_CONFIG_REGISTRY": "https://credential.invalid/",
            },
        ):
            return dict(
                recovery.build_source_assets(
                    self.source,
                    self.output,
                    python=sys.executable,
                    npm=npm,
                    helm=helm,
                )
            )

    def records(self) -> list[dict[str, object]]:
        return [json.loads(line) for line in self.tool_log.read_text().splitlines()]

    def test_builds_exact_source_assets_with_closed_tool_authority(self) -> None:
        calls: list[tuple[str, ...]] = []
        real_tool = recovery._run_tool

        def record(arguments, **keywords):
            calls.append(tuple(arguments))
            return real_tool(arguments, **keywords)

        with patch.object(recovery, "_run_tool", side_effect=record):
            metadata = self.build()

        self.assertEqual(
            metadata,
            {
                "version": "0.84.2",
                "chart_version": "0.87.2",
                "python_sdk_version": "0.84.0",
                "typescript_sdk_version": "0.84.0",
                "bedrock_instrumentation_version": "0.1.0",
                "revision": recovery.RELEASE_REVISION,
                "source_date": "2026-10-04T21:55:18+05:30",
                "platforms": ("linux/amd64", "linux/arm64"),
            },
        )
        self.assertEqual({path.name for path in self.output.iterdir()}, set(recovery.ARTIFACT_NAMES))
        self.assertTrue(all(path.is_file() and not path.is_symlink() for path in self.output.iterdir()))

        flattened = [item for command in calls for item in command]
        self.assertNotIn("docker", flattened)
        self.assertNotIn("buildx", flattened)
        self.assertTrue(any(command[1:3] == ("scripts/installation_kit.py", "build") for command in calls))
        self.assertTrue(any(command[1] == "scripts/package_licensed_chart.py" for command in calls))
        records = self.records()
        npm_arguments = [record["arguments"] for record in records if str(record["tool"]).startswith("fake-npm")]
        self.assertEqual(
            npm_arguments,
            [
                ["ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                ["run", "build"],
                ["pack", "--pack-destination", calls[-1][-1]],
            ],
        )
        for record in records:
            self.assertFalse(record["npmToken"])
            self.assertFalse(record["nodeOptions"])
            self.assertFalse(record["pythonPath"])
        for name in ("userconfig", "globalconfig", "cache"):
            selected = [record[name] for record in records if str(record["tool"]).startswith("fake-npm")]
            self.assertTrue(all(isinstance(value, str) and value for value in selected))
            self.assertTrue(all(not str(value).startswith(str(self.output)) for value in selected))

        npm_records = [
            record for record in records if str(record["tool"]).startswith("fake-npm")
        ]
        self.assertTrue(all(record["packageLock"] for record in npm_records))
        self.assertTrue(
            all(
                not Path(str(record["cwd"])).is_relative_to(self.source)
                for record in npm_records
            )
        )

    def test_dirty_source_is_rejected_before_output(self) -> None:
        (self.source / "README.md").write_text("dirty source", encoding="utf-8")
        with self.assertRaisesRegex(recovery.SourceAssetRecoveryError, "source.dirty"):
            self.build()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.tool_log.exists())

    def test_recreated_or_moved_tag_is_rejected(self) -> None:
        run("git", "tag", "--delete", recovery.RELEASE_TAG, cwd=self.source)
        run("git", "tag", recovery.RELEASE_TAG, recovery.RELEASE_REVISION, cwd=self.source)
        with self.assertRaisesRegex(recovery.SourceAssetRecoveryError, "source.tag-mismatch"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_extra_staged_artifact_fails_without_publishing(self) -> None:
        with self.assertRaisesRegex(
            recovery.SourceAssetRecoveryError, "output.inventory-invalid"
        ):
            self.build(extra=True)
        self.assertFalse(self.output.exists())

    def test_ignored_operator_credentials_never_enter_archives(self) -> None:
        protected = self.source / ".iip"
        protected.mkdir()
        (protected / "credentials.json").write_text(
            "operator-secret-sentinel", encoding="utf-8"
        )
        (self.source / ".env").write_text(
            "provider-secret-sentinel", encoding="utf-8"
        )
        self.build()

        forbidden = (b"operator-secret-sentinel", b"provider-secret-sentinel")
        for path in self.output.iterdir():
            with self.subTest(path=path.name), tarfile.open(path, mode="r:*") as archive:
                for member in archive.getmembers():
                    if not member.isfile():
                        continue
                    stream = archive.extractfile(member)
                    self.assertIsNotNone(stream)
                    assert stream is not None
                    content = stream.read()
                    for sentinel in forbidden:
                        self.assertNotIn(sentinel, content)

    def test_ignored_typescript_dist_cannot_enter_recovered_package(self) -> None:
        ignored = self.source / "sdks/typescript/dist/private.json"
        ignored.parent.mkdir(parents=True, exist_ok=True)
        sentinel = b"ignored-typescript-private-sentinel"
        ignored.write_bytes(sentinel)

        self.build()

        package = self.output / "iip-sdk-0.84.0.tgz"
        with tarfile.open(package, mode="r:gz") as archive:
            self.assertNotIn("package/dist/private.json", archive.getnames())
            for member in archive.getmembers():
                if member.isfile():
                    stream = archive.extractfile(member)
                    self.assertIsNotNone(stream)
                    assert stream is not None
                    self.assertNotIn(sentinel, stream.read())


if __name__ == "__main__":
    unittest.main()
