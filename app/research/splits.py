"""Pure deterministic membership; no evaluation, fitting, or holdout outcomes."""
from .models import (
    DevelopmentPartition, FoldPartition, ResearchDataset, WalkForwardPlan, _canonical,
)


def partition_development(
    dataset: ResearchDataset, plan: WalkForwardPlan,
) -> DevelopmentPartition:
    # Reconstruct at the callable boundary: frozen models still allow model_copy
    # bypasses and M6 contains mutable domain descendants.
    plan = _canonical(WalkForwardPlan, plan)
    dataset = _canonical(ResearchDataset, dataset)
    rows = sorted(dataset.observations, key=lambda row: (row.decision_at, str(row.observation_id)))
    partitions = []
    for fold in plan.folds:
        training, purged, unavailable, test = [], [], [], []
        for row in rows:
            at = row.decision_at
            if fold.train.start <= at < fold.train.end:
                known = row.label_available_at
                if known is None:
                    unavailable.append(row.observation_id)
                # Strict cutoff: equality belongs to test information time.
                elif known >= fold.test.start:
                    purged.append(row.observation_id)
                else:
                    training.append(row.observation_id)
            if fold.test.start <= at < fold.test.end:
                test.append(row.observation_id)
        partitions.append(FoldPartition(
            fold=fold, training_ids=tuple(training), purged_training_ids=tuple(purged),
            unavailable_training_ids=tuple(unavailable), test_ids=tuple(test),
        ))
    return DevelopmentPartition(
        strategy_id=dataset.strategy_id, strategy_version=dataset.strategy_version,
        plan=plan, folds=tuple(partitions),
    )
