"""CoVe Evidence Loop — Chain of Verification for citation grounding.

Per O1/U1: The 4-step Chain-of-Verification:
1. Draft finding + citations
2. Plan verification questions (one per cited article/case)
3. Execute independently — factored: each question answered in fresh context
4. Revise/drop finding per verification result

Phase 3: deterministic citation resolution via parser.
Phase 4+: adds LLM verification rounds.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from leggie.domain.models import Citation, Finding


@dataclass
class VerificationQuestion:
    """A single verification question for a cited source."""
    citation: Citation
    question: str = ""
    verified: bool = False
    evidence: str = ""


@dataclass
class CoVeResult:
    """Result of the Chain-of-Verification for a finding."""
    finding: Finding
    questions: list[VerificationQuestion] = field(default_factory=list)
    all_verified: bool = False
    verified_count: int = 0
    failed_count: int = 0


class CoVeVerifier:
    """Chain-of-Verification evidence loop.

    Factored variant: verification questions answered independently,
    blind to the draft, preventing rubber-stamping.
    """

    def __init__(self, citation_parser=None) -> None:
        self._citation_parser = citation_parser

    async def verify(self, finding: Finding) -> CoVeResult:
        """Run the full Chain-of-Verification on a finding.

        1. Plan: extract citations and formulate verification questions
        2. Execute: resolve each citation independently
        3. Revise: update finding based on results
        """
        # Step 1: Plan verification questions
        questions = self._plan_questions(finding)

        # Step 2: Execute independently (factored)
        questions = await self._execute_questions(questions)

        # Step 3: Compile results
        verified = [q for q in questions if q.verified]
        failed = [q for q in questions if not q.verified]

        return CoVeResult(
            finding=finding,
            questions=questions,
            all_verified=len(failed) == 0,
            verified_count=len(verified),
            failed_count=len(failed),
        )

    def _plan_questions(self, finding: Finding) -> list[VerificationQuestion]:
        """Extract citations from finding evidence and formulate questions."""
        questions: list[VerificationQuestion] = []

        for evidence in finding.evidence:
            if evidence.citation:
                questions.append(VerificationQuestion(
                    citation=evidence.citation,
                    question=f"Does the citation {evidence.citation.identifier} resolve correctly?",
                ))
            elif evidence.text_excerpt:
                # Try to parse citations from text
                if self._citation_parser:
                    parsed = self._citation_parser.parse(evidence.text_excerpt)
                    for cite in parsed:
                        questions.append(VerificationQuestion(
                            citation=cite,
                            question=f"Does the citation {cite.identifier} resolve correctly?",
                        ))

        return questions

    async def _execute_questions(self, questions: list[VerificationQuestion]) -> list[VerificationQuestion]:
        """Execute verification questions independently (factored).

        Phase 3: resolve via citation parser (deterministic).
        Phase 4+: also execute LLM-based verification.
        """
        for q in questions:
            if self._citation_parser:
                resolved = await self._citation_parser.resolve(q.citation)
                q.verified = resolved.resolved
                q.evidence = resolved.resolution_evidence or ""
            else:
                # Without a parser, trust the citation's resolved flag
                q.verified = q.citation.resolved
                q.evidence = q.citation.resolution_evidence or "no parser configured"
        return questions

    async def verify_batch(self, findings: list[Finding]) -> list[CoVeResult]:
        """Verify a batch of findings. Results are independent per finding."""
        return [await self.verify(f) for f in findings]
