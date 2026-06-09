"""FridaTransport — unified abstract interface for Frida communication.

Two implementations:
  - FridaTransportCli:     subprocess stdin/stdout (NIS bypass)
  - FridaTransportBinding: Python frida module (normal apps)
"""
from abc import ABC, abstractmethod


class FridaTransport(ABC):
    """Abstract transport for Frida-to-app communication."""

    @abstractmethod
    def connect(self, serial: str, package: str, script_path: str) -> None:
        """Establish connection to target app process."""

    @abstractmethod
    def send_message(self, uid: str, text: str, timeout: float = 5.0) -> dict:
        """Send IM message. Returns {success: bool, error: str}."""

    @abstractmethod
    def capture_key(self, timeout: float = 30.0) -> dict | None:
        """Block until KEY_JSON captured. Returns {key_hex, iv_hex, headers} or None."""

    @abstractmethod
    def disconnect(self) -> None:
        """Clean up connection."""

    @abstractmethod
    def is_running(self) -> bool:
        """Return True if transport is active."""
