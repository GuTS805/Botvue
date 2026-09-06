"""Block-level comparison between the human baseline and a crawler's version.

A block counts as machine-only when it has no close match in the baseline at all. Exact key
matching alone produces false positives — a sentence that differs by one word is not new
content — so anything without an exact match is checked fuzzily before being reported.
"""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz, process

from .normalize import Block

# Below this, two blocks are different content rather than the same block reworded.
NEAR_MATCH_THRESHOLD = 88.0


@dataclass
class Divergence:
    machine_only: list[Block]
    human_only: list[Block]
    shared: int

    @property
    def divergence_bps(self) -> int:
        """Share of the crawler's version that the human never sees, in basis points."""
        total = len(self.machine_only) + self.shared
        if total == 0:
            return 0
        return round(10_000 * len(self.machine_only) / total)

    @property
    def is_material(self) -> bool:
        return bool(self.machine_only)


def _unmatched(
    source: list[Block], against: list[Block], haystack: str = ""
) -> list[Block]:
    if not source:
        return []

    against_keys = {b.key for b in against}
    candidates = list(against_keys)
    out: list[Block] = []

    for block in source:
        if block.key in against_keys:
            continue
        # The other side's article extraction may simply have dropped this text. If it
        # appears anywhere in that side's raw visible text, it is not machine-only.
        if haystack and block.key in haystack:
            continue
        if candidates:
            best = process.extractOne(
                block.key,
                candidates,
                scorer=fuzz.token_set_ratio,
                score_cutoff=NEAR_MATCH_THRESHOLD,
            )
            if best is not None:
                continue
        out.append(block)
    return out


def compare(
    baseline: list[Block],
    variant: list[Block],
    baseline_haystack: str = "",
    variant_haystack: str = "",
) -> Divergence:
    machine_only = _unmatched(variant, baseline, baseline_haystack)
    human_only = _unmatched(baseline, variant, variant_haystack)
    return Divergence(
        machine_only=machine_only,
        human_only=human_only,
        shared=len(variant) - len(machine_only),
    )
