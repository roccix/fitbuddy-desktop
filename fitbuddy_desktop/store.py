"""Shared on-disk state between the sync daemon and the GUI.

The phone is the source of truth. The PC keeps the last snapshot the phone uploaded plus a queue
of operations created on the PC that the phone has not acknowledged yet.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import secrets
import time
import uuid
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1
DEFAULT_PORT = 8765
TOKEN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def default_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "fitbuddy-desktop"


def new_token() -> str:
    raw = "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(16))
    return "-".join(raw[i:i + 4] for i in range(0, 16, 4))


def normalize_token(token: str) -> str:
    return "".join(ch for ch in token.upper() if ch.isalnum())


class Store:
    """File-backed state guarded by an advisory lock so daemon and GUI can share it."""

    def __init__(self, directory: Path | None = None):
        self.dir = Path(directory) if directory else default_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        self._lock_path = self.dir / ".lock"

    # --- low level -------------------------------------------------------------------------

    @contextlib.contextmanager
    def locked(self):
        with open(self._lock_path, "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _read(self, name: str, default: Any) -> Any:
        try:
            with open(self.dir / name, encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return default
        except json.JSONDecodeError:
            return default

    def _write(self, name: str, value: Any) -> None:
        path = self.dir / name
        tmp = path.with_suffix(path.suffix + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    # --- config ----------------------------------------------------------------------------

    def config(self) -> dict:
        with self.locked():
            cfg = self._read("config.json", {})
            changed = False
            if not cfg.get("token"):
                cfg["token"] = new_token()
                changed = True
            cfg.setdefault("port", DEFAULT_PORT)
            cfg.setdefault("allow_lan", False)
            if changed:
                self._write("config.json", cfg)
            return cfg

    def set_config(self, key: str, value: Any) -> None:
        with self.locked():
            cfg = self._read("config.json", {})
            cfg[key] = value
            self._write("config.json", cfg)

    def regenerate_token(self) -> str:
        with self.locked():
            cfg = self._read("config.json", {})
            cfg["token"] = new_token()
            self._write("config.json", cfg)
            return cfg["token"]

    # --- snapshot --------------------------------------------------------------------------

    def snapshot(self) -> dict | None:
        return self._read("snapshot.json", None)

    def meta(self) -> dict:
        return self._read("meta.json", {})

    def apply_sync(
        self,
        acked: list[str] | None = None,
        snapshot: dict | None = None,
        snapshot_hash: str | None = None,
        app_version: str | None = None,
    ) -> tuple[list[dict], bool]:
        """Records one phone request atomically; returns (pending ops, need_snapshot).

        Acks and the snapshot that already contains those entries land together, so the GUI
        never shows an entry both as queued and as synced.
        """
        with self.locked():
            meta = self._read("meta.json", {})
            now = int(time.time() * 1000)
            if snapshot is not None:
                self._write("snapshot.json", snapshot)
                meta["snapshot_hash"] = snapshot_hash
                meta["snapshot_at"] = now
            ops = self._read("ops.json", [])
            if acked:
                done = set(acked)
                ops = [op for op in ops if op["id"] not in done]
                self._write("ops.json", ops)
            meta["last_contact_at"] = now
            if app_version:
                meta["app_version"] = app_version
            self._write("meta.json", meta)
            need_snapshot = snapshot is None and (
                not (self.dir / "snapshot.json").exists()
                or meta.get("snapshot_hash") != snapshot_hash
            )
            return ops, need_snapshot

    # --- op queue --------------------------------------------------------------------------

    def ops(self) -> list[dict]:
        return self._read("ops.json", [])

    def enqueue(self, op_type: str, **payload: Any) -> dict:
        op = {"id": str(uuid.uuid4()), "type": op_type, "createdAt": int(time.time() * 1000)}
        op.update({k: v for k, v in payload.items() if v is not None})
        with self.locked():
            ops = self._read("ops.json", [])
            ops.append(op)
            self._write("ops.json", ops)
        return op

    def cancel(self, op_id: str) -> bool:
        with self.locked():
            ops = self._read("ops.json", [])
            kept = [op for op in ops if op["id"] != op_id]
            self._write("ops.json", kept)
            return len(kept) != len(ops)
