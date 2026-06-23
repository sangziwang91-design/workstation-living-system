"""Workstation Living System package."""

from ._version import __version__
from .v2_runtime import LivingSystemV2 as LivingSystem

__all__ = ["LivingSystem", "__version__"]
