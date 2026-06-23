"""Workstation Living System package."""

from ._version import __version__
from .decision_runtime import DecisionAwareRuntime as LivingSystem

__all__ = ["LivingSystem", "__version__"]
