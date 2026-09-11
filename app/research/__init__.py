"""Offline M8 executable-label, partition and research-evidence boundaries."""
from .models import (
    DevelopmentPartition, FoldPartition, FrozenHoldout, ResearchDataset,
    ResearchInterval, ResearchObservation, WalkForwardFold, WalkForwardPlan,
)
from .splits import partition_development

__all__ = [
    'DevelopmentPartition', 'FoldPartition', 'FrozenHoldout', 'ResearchDataset',
    'ResearchInterval', 'ResearchObservation', 'WalkForwardFold', 'WalkForwardPlan',
    'partition_development',
]

from .evidence import (
    BootstrapConfig, BootstrapReport, ConfidenceInterval, CriterionCheck,
    DevelopmentOOSReport, EvidenceStatus, FoldOOSReport, FrozenResearchProtocol,
    HoldoutEvidenceReport, ResearchEvidenceCriteria, ResearchEvidenceReport,
)
from .evaluation import (
    build_research_evidence, evaluate_development_oos, evaluate_frozen_holdout,
)

__all__ += [
    'BootstrapConfig', 'BootstrapReport', 'ConfidenceInterval', 'CriterionCheck',
    'DevelopmentOOSReport', 'EvidenceStatus', 'FoldOOSReport', 'FrozenResearchProtocol',
    'HoldoutEvidenceReport', 'ResearchEvidenceCriteria', 'ResearchEvidenceReport',
    'build_research_evidence', 'evaluate_development_oos', 'evaluate_frozen_holdout',
]
