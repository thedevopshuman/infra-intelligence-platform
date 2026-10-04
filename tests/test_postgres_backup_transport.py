from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "deploy" / "helm" / "infra-intelligence"
PRODUCTION_VALUES = CHART / "examples" / "production-core.values.yaml"
FIXED_CA_PATH = "/var/run/iip/database-ca/ca.crt"


class PostgresBackupTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        rendered = subprocess.run(
            [
                "helm",
                "template",
                "backup-transport-test",
                str(CHART),
                "--namespace",
                "iip-system",
                "--values",
                str(PRODUCTION_VALUES),
                "--show-only",
                "templates/backup-configmap.yaml",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if rendered.returncode != 0:
            raise RuntimeError(f"failed to render backup ConfigMap: {rendered.stderr}")
        marker = "  backup.sh: |\n"
        if marker not in rendered.stdout:
            raise RuntimeError("rendered backup ConfigMap did not contain backup.sh")
        cls.rendered_script = textwrap.dedent(rendered.stdout.split(marker, 1)[1])

    def _script_with_test_ca(self, ca_path: Path) -> str:
        self.assertEqual(self.rendered_script.count(FIXED_CA_PATH), 3)
        return self.rendered_script.replace(FIXED_CA_PATH, str(ca_path))

    @staticmethod
    def _write_stub_clients(root: Path) -> Path:
        binaries = root / "bin"
        binaries.mkdir()
        pg_dump = binaries / "pg_dump"
        pg_dump.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            ": >\"$IIP_TEST_PG_DUMP_MARKER\"\n"
            "{\n"
            "  printf 'sslmode=%s\\n' \"${PGSSLMODE-}\"\n"
            "  printf 'sslrootcert=%s\\n' \"${PGSSLROOTCERT-}\"\n"
            "  printf 'gssencmode=%s\\n' \"${PGGSSENCMODE-}\"\n"
            "  printf 'host=%s\\n' \"${PGHOST-}\"\n"
            "  printf 'hostaddr=%s\\n' \"${PGHOSTADDR-}\"\n"
            "  printf 'service=%s\\n' \"${PGSERVICE-}\"\n"
            "  printf 'servicefile=%s\\n' \"${PGSERVICEFILE-}\"\n"
            "  printf 'sslcertmode=%s\\n' \"${PGSSLCERTMODE-}\"\n"
            "  printf 'sslmin=%s\\n' \"${PGSSLMINPROTOCOLVERSION-}\"\n"
            "  printf 'sslkeylogfile=%s\\n' \"${PGSSLKEYLOGFILE-}\"\n"
            "  printf 'database_env=%s\\n' \"${IIP_DATABASE_URL-}\"\n"
            "  printf 'transport_env=%s\\n' \"${IIP_DATABASE_TRANSPORT_MODE-}\"\n"
            "  printf 'ca_env=%s\\n' \"${IIP_DATABASE_CA_PATH-}\"\n"
            "} >\"$IIP_TEST_CAPTURE\"\n"
            "for argument in \"$@\"; do\n"
            "  case \"$argument\" in\n"
            "    --file=*) printf '%s\\n' dump >\"${argument#--file=}\" ;;\n"
            "  esac\n"
            "done\n",
            encoding="utf-8",
        )
        pg_restore = binaries / "pg_restore"
        pg_restore.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        pg_dump.chmod(0o755)
        pg_restore.chmod(0o755)
        return binaries

    def _run(self, database_url: str, root: Path) -> subprocess.CompletedProcess[str]:
        ca_path = root / "database-ca.crt"
        ca_path.write_text("test-only-ca\n", encoding="utf-8")
        binaries = self._write_stub_clients(root)
        backup_directory = root / "backups"
        backup_directory.mkdir()
        env = {
            **os.environ,
            "PATH": f"{binaries}:{os.environ['PATH']}",
            "HOSTNAME": "backup-test-host",
            "IIP_BACKUP_DIRECTORY": str(backup_directory),
            "IIP_BACKUP_PREFIX": "transport-test",
            "IIP_DATABASE_URL": database_url,
            "IIP_DATABASE_TRANSPORT_MODE": "verify-full",
            "IIP_DATABASE_CA_PATH": str(ca_path),
            "IIP_TEST_CAPTURE": str(root / "transport.txt"),
            "IIP_TEST_PG_DUMP_MARKER": str(root / "pg-dump-launched"),
            "PGHOST": "/tmp/ambient-socket",
            "PGHOSTADDR": "127.0.0.1",
            "PGSERVICE": "ambient-service",
            "PGSERVICEFILE": "/tmp/ambient-service.conf",
            "PGSSLCERTMODE": "disable",
            "PGSSLKEYLOGFILE": "/tmp/ambient-tls.keys",
            "PGSSLMINPROTOCOLVERSION": "TLSv1",
            "PGSSLMODE": "disable",
            "PGSSLROOTCERT": "/tmp/ambient-ca.crt",
        }
        return subprocess.run(
            ["/bin/sh"],
            input=self._script_with_test_ca(ca_path),
            cwd=ROOT,
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

    def test_ambiguous_or_policy_bearing_dsns_fail_before_pg_dump(self) -> None:
        invalid_urls = {
            "keyword-spaced-equals": (
                "host=database.private dbname=iip sslmode = disable "
                "password=do-not-log"
            ),
            "keyword-quoted-token": (
                "host='database.private' dbname='iip' "
                "'sslmode=disable' password='do-not-log'"
            ),
            "keyword-newline": (
                "host=database.private dbname=iip sslmode\n=disable "
                "password=do-not-log"
            ),
            "uri-policy-query": (
                "postgresql://iip:do-not-log@database.private/iip?sslmode=disable"
            ),
            "uri-encoded-policy-key": (
                "postgresql://iip:do-not-log@database.private/iip?ssl%6dode=disable"
            ),
            "uri-service-query": (
                "postgresql://iip:do-not-log@database.private/iip?service=ambient"
            ),
            "uri-empty-host": "postgresql:///iip",
            "uri-encoded-socket": (
                "postgresql://iip:do-not-log@%2Fvar%2Frun%2Fpostgresql/iip"
            ),
            "uri-mixed-socket-targets": (
                "postgresql://iip:do-not-log@database.private:5432,"
                "%2Fvar%2Frun%2Fpostgresql/iip"
            ),
            "uri-hostaddr-query": (
                "postgresql://iip:do-not-log@database.private/iip?"
                "hostaddr=127.0.0.1"
            ),
        }

        for name, database_url in invalid_urls.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                completed = self._run(database_url, root)

                self.assertNotEqual(completed.returncode, 0)
                self.assertFalse((root / "pg-dump-launched").exists())
                self.assertFalse((root / "transport.txt").exists())
                self.assertEqual(
                    completed.stderr,
                    "database DSN must not set transport policy\n",
                )
                output = completed.stdout + completed.stderr
                self.assertNotIn("do-not-log", output)
                self.assertNotIn("database.private", output)

    def test_valid_multi_host_uri_forces_exact_verify_full_policy(self) -> None:
        database_url = (
            "postgresql://iip:p%40ss%2Fword@database-a.example.test:5432,"
            "database-b.example.test:5432/iip"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            completed = self._run(database_url, root)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue((root / "pg-dump-launched").is_file())
            captured = (root / "transport.txt").read_text(encoding="utf-8")
            self.assertEqual(
                captured,
                "\n".join(
                    (
                        "sslmode=verify-full",
                        f"sslrootcert={root / 'database-ca.crt'}",
                        "gssencmode=disable",
                        "host=iip-database-host-required.invalid",
                        "hostaddr=",
                        "service=",
                        "servicefile=",
                        "sslcertmode=",
                        "sslmin=",
                        "sslkeylogfile=",
                        "database_env=",
                        "transport_env=",
                        "ca_env=",
                        "",
                    )
                ),
            )
            output = completed.stdout + completed.stderr
            self.assertNotIn("p%40ss%2Fword", output)
            self.assertNotIn("database-a.example.test", output)


if __name__ == "__main__":
    unittest.main()
