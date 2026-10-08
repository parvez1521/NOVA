"""Credential storage boundary for future connector adapters.

Connector code never writes OAuth tokens to SQLite or returns them to the UI.
The production store delegates to the macOS Keychain through the fixed
``security`` executable. Tests use the in-memory implementation below.
"""

from __future__ import annotations

import platform
import subprocess
from abc import ABC, abstractmethod


class CredentialStore(ABC):
    @abstractmethod
    def has(self, connector: str) -> bool:
        """Return whether a credential exists without exposing its value."""

    @abstractmethod
    def get(self, connector: str) -> str | None:
        """Read a credential for backend connector code only."""

    @abstractmethod
    def set(self, connector: str, value: str) -> None:
        """Store a credential in the platform secure store."""

    @abstractmethod
    def delete(self, connector: str) -> None:
        """Delete a stored credential."""


class MacOSKeychainStore(CredentialStore):
    service_prefix = "local.nova.connector"
    account = "default"

    def __init__(self, *, executable: str = "/usr/bin/security") -> None:
        self.executable = executable

    def _target(self, connector: str) -> list[str]:
        return [self.executable, "find-generic-password", "-s", f"{self.service_prefix}.{connector}", "-a", self.account]

    def has(self, connector: str) -> bool:
        if platform.system() != "Darwin":
            return False
        result = subprocess.run(self._target(connector), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=3)
        return result.returncode == 0

    def get(self, connector: str) -> str | None:
        if platform.system() != "Darwin":
            return None
        result = subprocess.run([*self._target(connector), "-w"], capture_output=True, text=True, check=False, timeout=3)
        return result.stdout.rstrip("\n") if result.returncode == 0 else None

    def set(self, connector: str, value: str) -> None:
        if platform.system() != "Darwin":
            raise RuntimeError("macOS Keychain is unavailable on this platform")
        subprocess.run([*self._target(connector), "-w", value, "-U"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=3)

    def delete(self, connector: str) -> None:
        if platform.system() != "Darwin":
            return
        subprocess.run([self.executable, "delete-generic-password", "-s", f"{self.service_prefix}.{connector}", "-a", self.account], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=3)


class MemoryCredentialStore(CredentialStore):
    """Non-production store used by contract tests."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def has(self, connector: str) -> bool:
        return connector in self.values

    def get(self, connector: str) -> str | None:
        return self.values.get(connector)

    def set(self, connector: str, value: str) -> None:
        self.values[connector] = value

    def delete(self, connector: str) -> None:
        self.values.pop(connector, None)
