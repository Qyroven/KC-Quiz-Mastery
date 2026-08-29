"""Private, atomic authentication state for the local-only browser QA bridge.

Not shipped with the runtime. The operator supplies a private directory outside
the served apps. No credential is printed, and malformed/mis-scoped files fail
closed rather than silently replacing an identity store.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
from pathlib import Path

STATE_FILENAME = "auth-state.v1.json"
MAX_STATE_BYTES = 4 * 1024 * 1024


class PrivateQAAuthStore:
    def __init__(self, directory: Path, context: dict):
        self.directory = Path(directory)
        self.context = dict(context)
        if not self.directory.is_absolute() or ".." in self.directory.parts:
            raise ValueError("QA auth store requires an absolute private directory")
        self._check_directory()

    def _check_directory(self) -> None:
        # Do not resolve a supplied symlink into an apparently acceptable path.
        for component in [*reversed(self.directory.parents), self.directory]:
            info = component.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ValueError("QA auth store path must not contain symlinks")
        info = self.directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise ValueError("QA auth store directory must be owned by this user with mode 0700")

    def _open_directory(self) -> int:
        self._check_directory()
        descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            os.close(descriptor)
            raise ValueError("QA auth store directory permissions changed")
        return descriptor

    @staticmethod
    def _check_file(directory_fd: int) -> bool:
        try:
            info = os.stat(STATE_FILENAME, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise ValueError(
                "QA auth state must be an owned, non-linked regular file with mode 0600"
            )
        return True

    def load(self) -> dict | None:
        directory_fd = self._open_directory()
        try:
            if not self._check_file(directory_fd):
                return None
            descriptor = os.open(STATE_FILENAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
            with os.fdopen(descriptor, "rb") as handle:
                info = os.fstat(handle.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_nlink != 1
                ):
                    raise ValueError("QA auth state permissions changed")
                raw = handle.read(MAX_STATE_BYTES + 1)
            if len(raw) > MAX_STATE_BYTES:
                raise ValueError("QA auth state exceeds the local size limit")
            try:
                state = json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                raise ValueError("QA auth state is invalid; operator recovery is required") from exc
            if not isinstance(state, dict) or state.get("schema_version") != "qa-auth-state.v1":
                raise ValueError("Unsupported QA auth state format")
            if state.get("context") != self.context:
                raise ValueError(
                    "QA auth state belongs to a different course, database, or app origin"
                )
            return state
        finally:
            os.close(directory_fd)

    def save(self, state: dict) -> None:
        if (
            state.get("context") != self.context
            or state.get("schema_version") != "qa-auth-state.v1"
        ):
            raise ValueError("Refusing mis-scoped QA auth state")
        raw = json.dumps(state, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        if len(raw) > MAX_STATE_BYTES:
            raise ValueError("QA auth state exceeds the local size limit")
        directory_fd = self._open_directory()
        temporary = ".auth-state-" + secrets.token_hex(12) + ".tmp"
        created = False
        try:
            self._check_file(directory_fd)
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
            created = True
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            self._check_file(directory_fd)
            os.replace(temporary, STATE_FILENAME, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            created = False
            os.fsync(directory_fd)
        finally:
            if created:
                os.unlink(temporary, dir_fd=directory_fd)
            os.close(directory_fd)
