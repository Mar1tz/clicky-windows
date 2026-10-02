"""HeyTora keeps itself up to date - see updates/service.py."""
from updates.service import Release, Updater, newer, parse_version, updater

__all__ = ["Release", "Updater", "newer", "parse_version", "updater"]
