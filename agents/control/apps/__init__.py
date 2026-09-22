"""One module per family of apps. Each exposes adapters() -> list of adapters
for the apps that are actually installed on this machine."""
from . import browsers, editors, media, windows_os

__all__ = ["browsers", "editors", "media", "windows_os"]
