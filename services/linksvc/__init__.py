"""Inter-dataset links (PRD §F6, M8).

Turns the semantic layer's per-pair signals into ranked, explained,
workflow-aware connections. Computes no raw signals itself.
"""

from datahub.linksvc.describe import Description, Relation, describe
from datahub.linksvc.rank import Link, rank, score, worth_surfacing
from datahub.linksvc.service import LinkPass, LinkService
from datahub.linksvc.signals import PairSignals, Signal, compute
from datahub.linksvc.weights import Weights, load

__all__ = [
    "Description",
    "Link",
    "LinkPass",
    "LinkService",
    "PairSignals",
    "Relation",
    "Signal",
    "Weights",
    "compute",
    "describe",
    "load",
    "rank",
    "score",
    "worth_surfacing",
]
