"""Workstation Living System.

A bounded experimental runtime for configured observation, evidence-grounded
state, governed action, and measured adaptation. It does not claim subjective
consciousness, genuine emotion, AGI, or unrestricted self-modification.
"""

from . import learning as _learning_module
from . import skills as _skills_module
from ._version import __version__
from .evidence_gates import EvidenceBoundLearningSystem, EvidenceBoundSkillLibrary

# Install the evidence-bound implementations before runtime.py imports the module
# symbols. This keeps one canonical LivingSystem while preventing machine-stage
# growth transitions from bypassing persisted experiment evidence.
setattr(_skills_module, "SkillLibrary", EvidenceBoundSkillLibrary)
setattr(_learning_module, "LearningSystem", EvidenceBoundLearningSystem)

from .runtime import LivingSystem  # noqa: E402

__all__ = ["LivingSystem", "__version__"]
