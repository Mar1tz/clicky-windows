"""The Podcast production extension: every episode out on time and complete.

    create_extension() -> PodcastExtension   (see pack.py)
"""
from __future__ import annotations


def create_extension():
    from .pack import PodcastExtension
    return PodcastExtension()
