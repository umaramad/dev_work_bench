"""SSH-module models."""

from __future__ import annotations

from dataclasses import dataclass, field

from devworkbench.models.base import Model


@dataclass
class SshProfile(Model):
    id: str = ""
    host: str = ""
    user: str = "dev"
    port: int = 22
    key_path: str = ""
    # passphrase lives in the macOS Keychain, never in the profile.


@dataclass
class RemoteFile(Model):
    name: str = ""
    path: str = ""
    is_dir: bool = False
    size: int = 0
    modified: str = ""


@dataclass
class TransferTask(Model):
    remote_path: str = ""
    direction: str = "upload"     # upload | download
    local_path: str = ""
    progress: int = 0
    status: str = "queued"        # queued | running | done | failed
    size: int = 0
