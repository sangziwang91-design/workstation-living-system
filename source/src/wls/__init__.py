"""Workstation Living System.

A bounded functional software-life runtime. It does not claim subjective
consciousness or genuine emotion. Its observable claims are limited to the
implemented perception, state, memory, learning, action, and governance loops.
"""

from ._version import __version__
from .runtime import LivingSystem

__all__ = ["LivingSystem", "__version__"]
