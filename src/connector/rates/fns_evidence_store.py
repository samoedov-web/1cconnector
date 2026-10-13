"""Content-addressed storage for original FNS evidence bytes.

The storage root must be a persistent, access-controlled volume.
This module does not commit accounting records or modify evidence.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from connector.rates.fns_transport import FnsArchive

SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class StoredFnsEvidence:
    zip_sha256: str
    json_sha256: str
    zip_path: Path
    json_path: Path
    member_name: str


class FnsEvidenceStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        if self.root.is_symlink():
            raise ValueError("Evidence root must not be a symlink")
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise ValueError("Evidence root must be a directory")

    def _path(self, digest: str) -> Path:
        if SHA256_PATTERN.fullmatch(digest) is None:
            raise ValueError("Invalid SHA-256 identifier")
        return self.root / "sha256" / digest[:2] / digest

    def read_verified(self, digest: str) -> bytes:
        path = self._path(digest)
        if path.is_symlink():
            raise ValueError("Evidence symlink rejected")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Stored evidence checksum mismatch")
        return raw

    def _put(self, payload: bytes, digest: str) -> Path:
        actual = hashlib.sha256(payload).hexdigest()
        if actual != digest:
            raise ValueError("Supplied evidence checksum mismatch")

        target = self._path(digest)
        parent = target.parent
        parent.mkdir(parents=True, exist_ok=True)

        if parent.is_symlink():
            raise ValueError("Evidence directory symlink rejected")

        if target.exists() or target.is_symlink():
            existing = self.read_verified(digest)
            if existing != payload:
                raise ValueError("Existing evidence differs")
            return target

        temporary = None
        try:
            fd, name = tempfile.mkstemp(
                prefix=".pending-",
                dir=parent,
            )
            temporary = Path(name)
            with os.fdopen(fd, "wb") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())

            try:
                # Hard-link is exclusive: never overwrite a prior object.
                os.link(temporary, target)
            except FileExistsError:
                existing = self.read_verified(digest)
                if existing != payload:
                    raise ValueError("Concurrent evidence conflict")

            directory_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

        self.read_verified(digest)
        return target

    def save_archive(self, archive: FnsArchive) -> StoredFnsEvidence:
        if hashlib.sha256(archive.raw_zip).hexdigest() != archive.sha256:
            raise ValueError("ZIP checksum mismatch")
        if hashlib.sha256(archive.raw_json).hexdigest() != archive.json_sha256:
            raise ValueError("JSON checksum mismatch")

        # Bind the JSON bytes to the actual member inside the ZIP.
        with zipfile.ZipFile(io.BytesIO(archive.raw_zip)) as z:
            members = z.infolist()
            if len(members) != 1:
                raise ValueError("Unexpected ZIP member count")
            member = members[0]
            if (
                member.filename != archive.member_name
                or member.is_dir()
                or member.flag_bits & 1
                or member.file_size > 100 * 1024 * 1024
            ):
                raise ValueError("Unexpected ZIP member")
            if z.read(member) != archive.raw_json:
                raise ValueError("ZIP/JSON content mismatch")

        zip_path = self._put(archive.raw_zip, archive.sha256)
        json_path = self._put(archive.raw_json, archive.json_sha256)

        return StoredFnsEvidence(
            zip_sha256=archive.sha256,
            json_sha256=archive.json_sha256,
            zip_path=zip_path,
            json_path=json_path,
            member_name=archive.member_name,
        )
