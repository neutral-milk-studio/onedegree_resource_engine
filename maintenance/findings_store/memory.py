"""In-memory findings store for tests and for building detection without a database."""

from __future__ import annotations

from contracts.findings import Finding, FindingRejected, finding_problems


class InMemoryFindingsStore:
    def __init__(self) -> None:
        self.findings: dict[str, Finding] = {}

    def save(self, finding: Finding) -> str:
        problems = finding_problems(finding)
        if problems:
            raise FindingRejected("; ".join(problems))
        self.findings[finding.id] = finding
        return finding.id
