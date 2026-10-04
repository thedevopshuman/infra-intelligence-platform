"""Cold recovery treats all volume bytes and tar metadata as untrusted input."""

from __future__ import annotations

import io
from contextlib import contextmanager
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deploy.community import recovery_volume as volume


def member(name: str, data: bytes = b"", *, directory: bool = False,
           uid: int = 70, gid: int = 70, mode: int = 0o600) -> tuple[tarfile.TarInfo, bytes]:
    value = tarfile.TarInfo(name)
    value.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
    value.size = 0 if directory else len(data)
    value.uid, value.gid, value.mode, value.mtime = uid, gid, mode, 1_700_000_000
    return value, data


def archive(*entries: tuple[tarfile.TarInfo, bytes], include_root: bool = True,
            format: int = tarfile.USTAR_FORMAT) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=format) as target:
        if include_root:
            root, _ = member(".", directory=True, mode=0o700)
            target.addfile(root)
        for metadata, data in entries:
            target.addfile(metadata, io.BytesIO(data))
    return output.getvalue()


class NonSeekable(io.BytesIO):
    def seekable(self) -> bool:
        return False


class CommunityVolumeArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        # Test fixtures use the host owner, not privileged chown. The production
        # allowlist is separately asserted and is never broadened by the CLI.
        self.ids = patch.object(volume, "ALLOWED_IDS", volume.ALLOWED_IDS | {os.getuid(), os.getgid()})
        self.ids.start()
        self.addCleanup(self.ids.stop)

    def assert_invalid(self, data: bytes, **limits) -> None:
        with self.assertRaisesRegex(volume.VolumeArchiveError, "^" + volume.ERROR_CODE + "$"):
            volume.inspect_archive(io.BytesIO(data), **limits)

    def test_roundtrip_streams_sorted_content_with_safe_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir(mode=0o700)
            (source / "z").write_bytes(b"last")
            (source / "directory").mkdir(mode=0o750)
            (source / "directory/a").write_bytes(b"first\x00binary")
            os.chmod(source / "directory/a", 0o640)
            os.utime(source / "directory/a", (1_700_000_000, 1_700_000_000))
            output = io.BytesIO()
            report = volume.export_volume(source, output)
            self.assertEqual(report, {"members": 4, "fileBytes": 16})
            second = io.BytesIO()
            volume.export_volume(source, second)
            self.assertEqual(output.getvalue(), second.getvalue())
            self.assertEqual(volume.inspect_archive(io.BytesIO(output.getvalue())), report)
            with tarfile.open(fileobj=io.BytesIO(output.getvalue())) as checked:
                self.assertEqual(checked.getnames(), [".", "directory", "directory/a", "z"])
                self.assertEqual(checked.getmember("directory/a").uname, "")
            destination = Path(temporary) / "restored"
            destination.mkdir(mode=0o755)
            self.assertEqual(volume.import_volume(destination, io.BytesIO(output.getvalue())), report)
            self.assertEqual((destination / "directory/a").read_bytes(), b"first\x00binary")
            self.assertEqual((destination / "z").read_bytes(), b"last")
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((destination / "directory").stat().st_mode), 0o750)
            self.assertEqual(stat.S_IMODE((destination / "directory/a").stat().st_mode), 0o640)
            self.assertEqual((destination / "directory/a").stat().st_mtime, 1_700_000_000)

    def test_root_only_empty_snapshot_is_valid(self) -> None:
        self.assertEqual(volume.inspect_archive(io.BytesIO(archive())), {"members": 1, "fileBytes": 0})

    def test_dotfiles_and_sparse_logical_content_roundtrip_without_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, restored = Path(temporary) / "source", Path(temporary) / "restored"
            source.mkdir(mode=0o700)
            restored.mkdir(mode=0o700)
            (source / ".cache").mkdir(mode=0o700)
            (source / ".cache/.state").write_bytes(b"hidden-state")
            sparse = source / "queue.db"
            logical_size = 2 * volume.CHUNK_SIZE + 17
            with sparse.open("wb") as stream:
                stream.seek(logical_size - 1)
                stream.write(b"x")
            output = io.BytesIO()
            report = volume.export_volume(source, output, max_bytes=logical_size + 12)
            self.assertEqual(report, {"members": 4, "fileBytes": logical_size + 12})
            self.assertGreater(len(output.getvalue()), logical_size)
            self.assertEqual(volume.import_volume(restored, io.BytesIO(output.getvalue())), report)
            self.assertEqual((restored / ".cache/.state").read_bytes(), b"hidden-state")
            self.assertEqual((restored / "queue.db").read_bytes(), bytes(logical_size - 1) + b"x")
            with self.assertRaises(volume.VolumeArchiveError):
                volume.export_volume(source, io.BytesIO(), max_bytes=logical_size + 11)

    def test_export_bounds_directory_discovery_before_opening_children(self) -> None:
        visited = []

        class Entry:
            name = "unused"

        def children():
            for index in range(100):
                visited.append(index)
                yield Entry()

        @contextmanager
        def directory_entries(descriptor):
            yield children()

        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(volume.os, "scandir", side_effect=directory_entries):
            with self.assertRaises(volume.VolumeArchiveError):
                volume.export_volume(temporary, io.BytesIO(), max_members=2)
        self.assertEqual(visited, [0, 1])

    def test_changed_same_sized_stream_cannot_pass_import_by_matching_counts(self) -> None:
        before = archive(member("file", b"original"))
        after = archive(member("file", b"modified"))
        self.assertEqual(volume.inspect_archive(io.BytesIO(before)),
                         volume.inspect_archive(io.BytesIO(after)))

        class ChangedOnRewind(io.BytesIO):
            def seek(self, offset, whence=0):
                if offset == 0 and whence == 0 and self.tell():
                    super().seek(0)
                    super().write(after)
                return super().seek(offset, whence)

        with tempfile.TemporaryDirectory() as temporary, patch.object(volume.os, "fchown"):
            destination = Path(temporary) / "restored"
            destination.mkdir(mode=0o700)
            with self.assertRaises(volume.VolumeArchiveError):
                volume.import_volume(destination, ChangedOnRewind(before))
            # Failure preserves only an incomplete owned destination for the
            # orchestrator to fence; it never reports this changed data valid.
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)

    def test_fixed_numeric_owner_allowlist_and_root_directory_mode(self) -> None:
        self.assertEqual(self.ids.temp_original, frozenset((0, 70, 472, 10001, 65534)))
        data = archive(member("db", b"x", uid=70, gid=70, mode=0o640),
                       member("queue", b"y", uid=10001, gid=10001),
                       member("prom", b"z", uid=65534, gid=65534),
                       member("grafana", b"g", uid=472, gid=472))
        with tempfile.TemporaryDirectory() as temporary, patch.object(volume.os, "fchown") as chown:
            volume.import_volume(temporary, io.BytesIO(data))
            owners = {(call.args[1], call.args[2]) for call in chown.call_args_list}
            self.assertEqual(owners, {(70, 70), (10001, 10001), (65534, 65534), (472, 472)})
        for entry in (member("file", uid=123456), member("file", gid=123456)):
            with self.subTest(entry=entry[0].uid):
                self.assert_invalid(archive(entry))

    def test_reject_archive_links_devices_fifos_and_extension_records(self) -> None:
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE,
                     tarfile.FIFOTYPE, tarfile.GNUTYPE_SPARSE, tarfile.XHDTYPE, tarfile.XGLTYPE,
                     tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK, tarfile.CONTTYPE):
            with self.subTest(kind=kind):
                entry = member("unsafe")
                entry[0].type = kind
                self.assert_invalid(archive(entry))
        long_path = "x" * 150
        self.assert_invalid(archive(member(long_path), format=tarfile.PAX_FORMAT))
        self.assert_invalid(archive(member("file"), format=tarfile.GNU_FORMAT))

    def test_reject_traversal_and_noncanonical_paths(self) -> None:
        for name in ("/absolute", "../escape", "nested/../escape", "./file", "a//b", "back\\slash",
                     "a/./b", "trailing/", "control\nname", "a/" * 65 + "file"):
            with self.subTest(name=name):
                self.assert_invalid(archive(member(name)))
        self.assert_invalid(archive(member("nested//", directory=True)))

    def test_reject_duplicate_missing_root_or_missing_directory_parent(self) -> None:
        for data in (
            archive(member("file"), member("file")),
            archive(member(".", directory=True)),
            archive(member("file"), include_root=False),
            archive(member(".", directory=False), include_root=False),
            archive(member("parent/file")),
            archive(member("parent"), member("parent/file")),
        ):
            self.assert_invalid(data)

    def test_reject_privilege_bits_and_directory_payload(self) -> None:
        for mode in (0o1600, 0o2600, 0o4600):
            self.assert_invalid(archive(member("file", mode=mode)))
        directory = member("nested", directory=True)
        directory[0].size = 1
        self.assert_invalid(archive((directory[0], b"x")))

    def test_reject_ignored_header_bytes_and_noncanonical_metadata(self) -> None:
        original = archive(member("file", b"data"))
        for start, value in ((10, b"hidden"), (100, b"0010700\0"), (265, b"host-user"),
                             (500, b"hidden")):
            header = bytearray(original[512:1024])
            header[start:start + len(value)] = value
            header[148:156] = b"        "
            header[148:156] = ("%06o\0 " % sum(header)).encode()
            self.assert_invalid(original[:512] + bytes(header) + original[1024:])

    def test_reject_truncation_corruption_and_nonzero_trailing_payload(self) -> None:
        valid = archive(member("file", b"secret-value"))
        for length in (0, 511, 512, 1024, 1030, 1536, 2048):
            self.assert_invalid(valid[:length])
        self.assert_invalid(valid + b"hidden-value")
        self.assert_invalid(valid + b"\0")
        self.assert_invalid(valid[:513] + bytes([valid[513] ^ 1]) + valid[514:])
        # Bytes outside a declared file's payload must be zero padding.
        self.assert_invalid(valid[:1036] + b"x" + valid[1037:])
        compressed = io.BytesIO()
        with tarfile.open(fileobj=compressed, mode="w:gz") as target:
            root, _ = member(".", directory=True)
            target.addfile(root)
        self.assert_invalid(compressed.getvalue())

    def test_member_and_file_byte_limits_are_checked_before_any_import_write(self) -> None:
        data = archive(member("file", b"abc"))
        self.assertEqual(volume.inspect_archive(io.BytesIO(data), max_bytes=3, max_members=2),
                         {"members": 2, "fileBytes": 3})
        self.assert_invalid(data, max_bytes=2)
        self.assert_invalid(data, max_members=1)
        for kwargs in ({"max_bytes": -1}, {"max_bytes": volume.HARD_MAX_BYTES + 1},
                       {"max_bytes": True}, {"max_members": 0}, {"max_members": 200001}):
            self.assert_invalid(data, **kwargs)
        with tempfile.TemporaryDirectory() as temporary:
            for malformed in (data + b"later-invalid", archive(member("good", b"a"), member("../bad"))):
                with self.assertRaises(volume.VolumeArchiveError):
                    volume.import_volume(temporary, io.BytesIO(malformed))
                self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_export_rejects_root_and_child_symlinks_and_regular_hardlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            root.mkdir(mode=0o700)
            linked = Path(temporary) / "linked"
            linked.symlink_to(root, target_is_directory=True)
            with self.assertRaises(volume.VolumeArchiveError):
                volume.export_volume(linked, io.BytesIO())
            (root / "symlink").symlink_to("/outside")
            with self.assertRaises(volume.VolumeArchiveError):
                volume.export_volume(root, io.BytesIO())
            (root / "symlink").unlink()
            (root / "file").write_bytes(b"x")
            os.link(root / "file", root / "hardlink")
            with self.assertRaises(volume.VolumeArchiveError):
                volume.export_volume(root, io.BytesIO())

    def test_export_rejects_fifo_socket_privilege_mode_and_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fifo = root / "fifo"
            os.mkfifo(fifo)
            with self.assertRaises(volume.VolumeArchiveError):
                volume.export_volume(root, io.BytesIO())
            fifo.unlink()
            (root / "socket").write_bytes(b"")
            original_stat = os.stat
            fields = list(original_stat(root / "socket"))
            fields[0] = stat.S_IFSOCK | 0o600

            def observed(path, **kwargs):
                return os.stat_result(fields) if path == "socket" else original_stat(path, **kwargs)

            # No socket needs to be opened by the ordinary offline test suite.
            with patch.object(volume.os, "stat", side_effect=observed):
                with self.assertRaises(volume.VolumeArchiveError):
                    volume.export_volume(root, io.BytesIO())
            (root / "socket").unlink()
            file = root / "file"
            file.write_bytes(b"123")
            # Some host filesystems silently strip setuid on chmod; exercise
            # the shared export/header metadata guard without relying on it.
            with self.assertRaises(volume.VolumeArchiveError):
                volume._metadata(0o4600, 70, 70)
            for kwargs in ({"max_bytes": 2}, {"max_members": 1}):
                with self.assertRaises(volume.VolumeArchiveError):
                    volume.export_volume(root, io.BytesIO(), **kwargs)

    def test_restore_never_overwrites_nonempty_or_symlink_root(self) -> None:
        data = archive(member("file", b"replacement"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            root.mkdir(mode=0o700)
            (root / "file").write_bytes(b"original")
            with self.assertRaises(volume.VolumeArchiveError):
                volume.import_volume(root, io.BytesIO(data))
            self.assertEqual((root / "file").read_bytes(), b"original")
            link = Path(temporary) / "link"
            link.symlink_to(root, target_is_directory=True)
            with self.assertRaises(volume.VolumeArchiveError):
                volume.import_volume(link, io.BytesIO(data))

    def test_mounted_archive_path_must_be_private_regular_and_single_link(self) -> None:
        data = archive(member("file", b"example"))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "snapshot.tar"
            path.write_bytes(data)
            os.chmod(path, 0o600)
            expected = {"members": 2, "fileBytes": 7}
            self.assertEqual(volume.inspect_archive(path), expected)
            destination = Path(temporary) / "destination"
            destination.mkdir(mode=0o700)
            with patch.object(volume.os, "fchown"):
                self.assertEqual(volume.import_volume(destination, path), expected)
            os.chmod(path, 0o644)
            with self.assertRaises(volume.VolumeArchiveError):
                volume.inspect_archive(path)
            os.chmod(path, 0o600)
            link = Path(temporary) / "link"
            link.symlink_to(path)
            with self.assertRaises(volume.VolumeArchiveError):
                volume.inspect_archive(link)
            os.link(path, Path(temporary) / "hardlink")
            with self.assertRaises(volume.VolumeArchiveError):
                volume.inspect_archive(path)

    def test_nonseekable_input_is_privately_spooled_and_bounded(self) -> None:
        data = archive(member("file", b"example"))
        with tempfile.TemporaryDirectory() as temporary, patch.object(volume.os, "fchown"):
            result = volume.import_volume(temporary, NonSeekable(data))
            self.assertEqual(result, {"members": 2, "fileBytes": 7})
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(volume.VolumeArchiveError):
                volume.import_volume(temporary, NonSeekable(b"x" * 20000), max_bytes=0, max_members=1)
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_cli_invalid_input_and_arguments_never_echo_sensitive_values(self) -> None:
        script = str(Path(volume.__file__))
        for arguments in (("check",), ("check", "--archive", "secret-location"),
                          ("check", "--max-bytes", "secret-value")):
            result = subprocess.run([sys.executable, script, *arguments], input=b"private-secret-input",
                                    capture_output=True, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, (volume.ERROR_CODE + "\n").encode())

    def test_cli_check_only_reports_aggregate_counts(self) -> None:
        result = subprocess.run([sys.executable, str(Path(volume.__file__)), "check"],
                                input=archive(member("private-file", b"private-content")),
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, b'{"fileBytes":15,"members":2}\n')


if __name__ == "__main__":
    unittest.main()
