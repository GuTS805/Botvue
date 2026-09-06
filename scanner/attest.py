"""Attestation, behind an interface.

Submission itself lives in the TypeScript package, because the Hedera SDK does. The gateway
must not care: it hands a record to an Attestor and gets back a receipt, and whether that
record reached consensus in-line, later, or not at all is the Attestor's problem.

`QueueAttestor` is the default. It writes the record to disk and returns immediately, which
keeps a slow or unavailable consensus round from delaying the verdict the agent is waiting
on — the attestation is evidence, not part of the decision.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

ATTEST_DIR = Path(__file__).resolve().parents[1] / "attestations"
PENDING_DIR = ATTEST_DIR / "pending"


@dataclass
class Attestation:
    domain: str
    url: str
    observedAt: str
    verdict: str
    rule: str
    agents: list[str]
    bodies: dict[str, str]
    wordRatio: dict[str, float]
    controlSimilarity: float | None
    v: int = 1

    def canonical(self) -> str:
        """Byte-for-byte stable, so the same observation always hashes the same way and a
        reader can confirm the record they fetched is the record that was written."""
        return json.dumps(
            {
                "v": self.v,
                "domain": self.domain,
                "url": self.url,
                "observedAt": self.observedAt,
                "verdict": self.verdict,
                "rule": self.rule,
                "agents": sorted(self.agents),
                "bodies": dict(sorted(self.bodies.items())),
                "wordRatio": dict(sorted(self.wordRatio.items())),
                "controlSimilarity": self.controlSimilarity,
            },
            separators=(",", ":"),
        )

    @property
    def id(self) -> str:
        return hashlib.sha256(self.canonical().encode()).hexdigest()[:16]


@dataclass
class Receipt:
    status: str
    id: str
    topic: str | None = None
    sequence: int | None = None
    detail: str = ""


class Attestor(Protocol):
    def submit(self, attestation: Attestation) -> Receipt: ...


class QueueAttestor:
    """Writes the record for the chain package to submit. Idempotent by record id, so
    re-checking the same unchanged page does not queue a duplicate."""

    def __init__(self, directory: Path = PENDING_DIR) -> None:
        self.directory = directory

    def submit(self, attestation: Attestation) -> Receipt:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{attestation.id}.json"
        if path.exists():
            return Receipt("queued", attestation.id, detail="already queued")
        path.write_text(attestation.canonical(), encoding="utf-8")
        return Receipt("queued", attestation.id, detail=str(path.name))


class NullAttestor:
    """For tests and for running the gateway with attestation switched off."""

    def submit(self, attestation: Attestation) -> Receipt:
        return Receipt("skipped", attestation.id, detail="attestation disabled")


def build(verdict, url: str, rule: str, agents: list[str], bodies: dict[str, str]) -> Attestation:
    return Attestation(
        domain=verdict.domain,
        url=url,
        observedAt=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        verdict=verdict.verdict,
        rule=rule,
        agents=agents,
        bodies=bodies,
        wordRatio=verdict.word_ratio,
        controlSimilarity=verdict.similarity.get("googlebot"),
    )
