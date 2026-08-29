# Leggie — Verification Chain Integrity Fix Plan

> Status: **IMPLEMENTED** (offline gates green — 567 passed, 1 skipped,
> 83.12% coverage; live smoke NOT run — see §8). Every finding in §2.4/§3.4/§4.4
> was verified fail-old/pass-new via `git stash push -- <files>` before this
> doc was updated, per this repo's standing discipline.
> Classes: **A × 3** (see per-defect headers — all three touch lens logic,
> CoVe, the citation parser, or the reranker, which `leggie-change-control` §1
> lists as class-A regardless of how small the diff looks).
> Layers touched: Domain (`domain/models/__init__.py`), Application
> (`application/agents/*_lens.py`, `services/cove_verifier.py`,
> `services/blackboard_aggregator.py`, `services/rerank.py`,
> `workflow/bill_analysis_flow.py`), Infrastructure
> (`infrastructure/citation/__init__.py`, `infrastructure/reasoner/adapter.py`).

This plan closes the three findings from the static defect audit of the
verification chain (D1–D3, full audit in-thread): a missing article identity
on `Finding` that makes CoVe's anti-hallucination gate silently no-op, a
citation-parser contract that leaks through a magic string, and a reranker
that swallows its own degradation. It is written against the **current**
head of `claude/leggie-concierge-mvp-lglq0k` (post PR #10 — rerank-order fix
and the local-gates infrastructure are already in).

---

## 0. Guiding design principles

Every choice below is picked from patterns **already load-bearing in this
repo** (`leggie-architecture-contract` §5 pattern map) rather than imported
from outside. Where a genuinely new principle is needed, it's named
explicitly and justified against the alternative.

| Principle | Where it applies | Why (not just "best practice") |
|---|---|---|
| **Entities carry their own identity** (DDD) | D1 | `Finding` already carries `id`, `lens`, `model`, `prompt_hash` as first-class identity/provenance fields. `article_id` is missing from that list even though every lens has it in scope at construction time (`analyze(self, article: Article)`). Reconstructing it later by parsing a free-text field is the actual defect — the fix is to stop discarding data the code already has. |
| **Make illegal states unrepresentable** | D2 | Considered for the full tri-state redesign; adopted in a lighter form (see §3.2) once tracing every real call site showed the heavier version bought nothing extra here. |
| **Expand → Migrate → (never forced to) Contract** | D1, D2 | Both are additive fields with safe defaults (`article_id: str = ""`, `checked: bool = False`). Nothing is deleted or renamed; every existing constructor call and every existing checkpoint/JSON blob on disk keeps parsing. "Contract" (removing the regex fallback) is explicitly **not** part of this plan — see §5. |
| **Single Responsibility / DRY** | D1 | `article_number()` extraction is currently re-implemented three times (`cove_verifier.py`, `blackboard_aggregator.py`, `bill_analysis_flow.py`) with copy-pasted regexes. Consolidated to one call site. |
| **Observer / Event Sourcing, applied where it's already the house style** | D3 | `agents/lens.py` already has `on_degradation: Callable[..., None]` → `Event(event_type=EventType.DEGRADED, ...)`. D3 extends that exact mechanism to `ModelBasedReranker` rather than inventing logging-only or a different callback shape. |
| **Port stability (non-negotiable #3)** | D1, D2, D3 | None of the three touch a Port's method signature. D1/D2 change Domain **value objects** (`Finding`, `Citation`) consumed by ports; D3 changes an **adapter/consumer** (`ModelBasedReranker`) of `RerankerPort`, not the port itself. Verified against `application/ports/citation_parser.py` and `application/ports/reranker.py` — neither needs to change. |
| **Domain purity (invariant #1)** | D1, D2 | Both new fields are plain data + one `@model_validator`, zero imports added to `leggie/domain/`. |

---

## 1. Sequencing note — domain models are their own class-A change

`leggie-change-control` non-negotiable #2: *"`Finding`, `IRAC`, `Confidence`
are not modified while remediating infrastructure defects. Domain changes
are their own class-A change with a plan doc."* `Citation` is the same kind
of frozen domain value object, so it's held to the same rule here even
though the skill doc only names the three it names.

Consequence for sequencing: the domain-model edits (§2.1, §3.2) are their
own commits, gated and reviewable independently of the consumer-side wiring
that uses the new fields (§2.2–§2.4, §3.3, §4). See §6 for the exact commit
list. (Note: this checkout has no `.claude/hooks/guard_pretooluse.py` —
the mechanical enforcement `leggie-change-control` §3b describes appears to
be local to the repo owner's machine, not committed here — so nothing will
physically block a combined edit. The sequencing below is followed anyway
because the *reason* for the rule — domain changes get disproportionate
blast radius for their size — doesn't depend on whether a hook enforces it.)

---

## 2. Defect 1 — `Finding` has no article identity

### 2.1 Diagnosis (recap)

| # | Root cause | Consequence |
|---|---|---|
| D1a | `Finding` (`domain/models/__init__.py:278-306`) has no `article_id` field, though every lens's `analyze(self, article: Article)` has `article.id` in scope at the moment it builds the `Finding`. | Nothing downstream can ask "which article is this finding about" except by parsing it back out of free text. |
| D1b | `CoVeVerifier.verify_batch` (`cove_verifier.py:150-153`) resolves the source-article text via `index.get(article_number(f.irac.issue), "")`. `IRACCandidate.issue` (`domain/models/structured_output.py:14`) is LLM free text with no schema requirement to contain "Άρθρο N". | Whenever the model's phrasing omits it, `source_text` silently comes back `""`, and the F3 verbatim-quote gate (`cove_verifier.py:211`) — the cheapest, most important anti-hallucination check in the whole chain — is skipped with **no log, no event**. |
| D1c | The same regex (`Άρθρο\s+(\d+)`) is re-implemented as a local closure in `blackboard_aggregator.py:26-46` (`_finding_similarity_article_aware`) and again in `bill_analysis_flow.py:536-567` (`_dedup_findings`), instead of reusing the one already-canonical `article_number()` that `bill_analysis_flow.py` already imports from `cove_verifier.py` for the *index-building* side. | Same fragility, second-order symptom: findings that both fail to state their article lose one discriminating signal during dedup. Also a plain DRY violation — three regexes to keep in sync forever. |

### 2.2 Design decision

**Chosen:** add `Finding.article_id: str = Field(default="")`, populate it at
every lens construction site (`article.id` is already a parameter), and add
one new function `article_number_of(finding: Finding) -> str` next to the
existing `article_number(text: str)` in `cove_verifier.py` that does
`finding.article_id or article_number(finding.irac.issue)`. All three
consumers call `article_number_of(f)` instead of maintaining their own
regex.

**Rejected alternative — tighten the regex / prompt instead:** make the LLM
prompt require "Άρθρο {id}:" as a literal prefix of `issue`, or make the
regex more permissive. Rejected because no prompt instruction is a
*guarantee* the way a structured field is — this is the same class of
mistake the defect already is (trusting free text to carry structured
information). It would also still leave three copies of the regex to keep
in sync.

**Rejected alternative — a dedicated `leggie/domain/article_ref.py` module:**
considered for the consolidated helper instead of `cove_verifier.py`.
Rejected because `article_number()` already has one canonical home that
`bill_analysis_flow.py` already imports from — moving it would touch an
extra import in a file that doesn't otherwise need to change, for no
behavioral gain. Revisit only if a fourth consumer outside
`application/services`/`application/workflow` ever needs it (a Domain-layer
home would then earn its keep by being importable from anywhere).

### 2.3 Fix steps

**Commit 1 — domain field (its own class-A change, per §1):**

```diff
--- a/leggie/domain/models/__init__.py
+++ b/leggie/domain/models/__init__.py
@@ class Finding(BaseModel):
     id: UUID = Field(default_factory=uuid4)
     finding_type: FindingType = Field(description="Category of finding (U3 typed)")
     irac: IRAC = Field(description="IRAC legal reasoning structure")
+    article_id: str = Field(
+        default="",
+        description="Article this finding is about. Empty for legacy/pre-fix "
+        "findings — consumers fall back to parsing 'Άρθρο N' out of irac.issue "
+        "via article_number_of().",
+    )
     severity: Severity = Field(default=Severity.MEDIUM)
```

**Commit 2 — populate it at every lens construction site.** Confirmed exact
diff for `constitutional_lens.py` (read in full this pass); the same
one-line addition applies at the equivalent `Finding(...)` call in
`economic_lens.py`, `eu_gdpr_lens.py`, `implementation_lens.py`,
`legal_coherence_lens.py` — confirmed via grep to share the identical
`Evidence(text_excerpt=c.verbatim_quote, ...)` / regex-fallback shape, but
**not individually opened this pass**; read each before applying, don't
copy-paste blind.

```diff
--- a/leggie/application/agents/constitutional_lens.py
+++ b/leggie/application/agents/constitutional_lens.py
@@ def _candidate_to_finding(self, c: IRACCandidate, article: Article) -> Finding:
         return Finding(
             finding_type=FindingType.CONSTITUTIONAL,
+            article_id=article.id,
             irac=IRAC(
@@ def _analyze_regex(self, article: Article) -> list[Finding]:  # ×3 Finding(...) blocks in this method
                 Finding(
                     finding_type=FindingType.CONSTITUTIONAL,
+                    article_id=article.id,
                     irac=IRAC(
```

**Commit 3 — consolidate the lookup, in the three consumers:**

```diff
--- a/leggie/application/services/cove_verifier.py
+++ b/leggie/application/services/cove_verifier.py
@@
 def article_number(text: str) -> str:
     """Extract the article number from free text (e.g. 'Άρθρο 83 ...' → '83')."""
     m = _ARTICLE_RE.search(text or "")
     return m.group(1) if m else ""
+
+
+def article_number_of(finding: Finding) -> str:
+    """The article a finding is about: its own article_id, or a best-effort
+    regex fallback for findings that predate that field."""
+    return finding.article_id or article_number(finding.irac.issue)
@@ async def verify_batch(
         for f in findings:
-            source = index.get(article_number(f.irac.issue), "")
+            source = index.get(article_number_of(f), "")
             results.append(await self.verify(f, source))
--- a/leggie/application/services/blackboard_aggregator.py
+++ b/leggie/application/services/blackboard_aggregator.py
@@
-from leggie.application.services.cove_verifier import CoVeVerifier
+from leggie.application.services.cove_verifier import CoVeVerifier, article_number_of
@@ def _finding_similarity_article_aware(a: Finding, b: Finding) -> float:
-    import re
-
-    _article_re = re.compile(r"Άρθρο\s+(\d+)", re.IGNORECASE)
-
-    def _article_prefix(f: Finding) -> str:
-        m = _article_re.search(f.irac.issue)
-        return m.group(1) if m else ""
-
     if (
         a.finding_type != b.finding_type
         or a.lens != b.lens
-        or _article_prefix(a) != _article_prefix(b)
+        or article_number_of(a) != article_number_of(b)
     ):
--- a/leggie/application/workflow/bill_analysis_flow.py
+++ b/leggie/application/workflow/bill_analysis_flow.py
@@
-from leggie.application.services.cove_verifier import CoVeVerifier, article_number
+from leggie.application.services.cove_verifier import CoVeVerifier, article_number, article_number_of
@@ def _dedup_findings(self, findings: list[Finding]) -> list[Finding]:
-        import re
-
-        _article_re = re.compile(r"Άρθρο\s+(\d+)", re.IGNORECASE)
-
-        def _article_prefix(finding: Finding) -> str:
-            m = _article_re.search(finding.irac.issue)
-            return m.group(1) if m else ""
-
         if not findings:
             return []

         def _finding_similarity(a: Finding, b: Finding) -> float:
             if (
                 a.finding_type != b.finding_type
                 or a.lens != b.lens
-                or _article_prefix(a) != _article_prefix(b)
+                or article_number_of(a) != article_number_of(b)
             ):
```

(`article_number` — the plain text→string version — stays imported/used
where `bill_analysis_flow.py` builds `article_index` itself:
`article_number(a.raw_text) or a.id`, keyed by `Article`, not `Finding`;
that call site is untouched.)

### 2.4 Tests

New file `tests/unit/application/test_finding_article_identity.py`,
regression-verified fail-old/pass-new per this repo's established discipline
(`git stash`, confirm red, `git stash pop`, confirm green):

1. `test_cove_f3_gate_fires_without_article_prefix_in_issue` — the reproducer
   from the audit (§ D1 fix package): a finding whose `irac.issue` never says
   "Άρθρο N" but carries `article_id="15"`; a fabricated quote must still be
   caught.
2. `test_article_number_of_prefers_article_id_over_regex` — direct unit test
   of the new helper: `article_id` set → used verbatim, even if `irac.issue`
   contains a *different* article number (guards against a future regression
   where someone "helpfully" re-adds a regex-first order).
3. `test_article_number_of_falls_back_for_legacy_findings` — `article_id=""`
   → old regex behavior, unchanged, so pre-fix checkpoints keep working.
4. `test_dedup_groups_by_article_id_not_regex` — two findings with the same
   `article_id` but issue text that would extract *different* numbers under
   the old regex must still be grouped correctly by the new path (exercises
   `blackboard_aggregator.py`'s and `bill_analysis_flow.py`'s dedup similarity
   functions).

---

## 3. Defect 2 — citation resolved/unresolved contract leaks a magic string

### 3.1 Diagnosis (recap)

`CoVeVerifier._check_citations` (`cove_verifier.py:265-288`) tells "disproven"
from "merely unverified" by testing whether the substring `"no resolution
index"` appears inside `Citation.resolution_evidence`, a free-text field
`CitationParserPort.resolve()`'s docstring (`ports/citation_parser.py:21-27`)
never actually constrains. It works today only because `GreekCitationParser`
(the one shipped adapter) happens to phrase it that way.

**Every real call site, traced this pass** (not just the two already known
from the audit):

| File | Role | `resolved` used how |
|---|---|---|
| `infrastructure/citation/__init__.py:112-134` | `GreekCitationParser.resolve()` — the only site that actually knows "checked vs not" | constructs `Citation(resolved=..., resolution_evidence=...)` |
| `application/services/cove_verifier.py:265-288` | the actual defect site | reads `resolution_evidence` string to infer checked-ness |
| `domain/specs/__init__.py:80-83, 108-114` | `CitationResolves` (Specification pattern) + `is_satisfied_by` | reads `citation.resolved` as a plain bool — **must keep working unchanged** |
| `infrastructure/reasoner/adapter.py:145-155` | deliberative-pipeline citation parsing from the external Reasoner backend's JSON | constructs `Citation(resolved=bool(raw.get("resolved", False)), ...)` — **a second, independent construction site**, and this pipeline explicitly skips CoVe entirely (`leggie-architecture-contract` §3, "Deliberately SKIPS Finding mapping, Skeptic, and CoVe"), so `resolved` here is never Leggie-checked at all — it just echoes an unaudited external claim |

That fourth row is the one the original audit didn't have in front of it and
that changes the design decision below.

### 3.2 Design decision

**Considered — tri-state `CitationResolutionStatus(StrEnum)`
(`VERIFIED`/`DISPROVEN`/`UNVERIFIED`) replacing `resolved: bool`, with
`resolved` kept as a `@computed_field @property` for backward compatibility**
(this is the textbook "make illegal states unrepresentable" answer, and it
matches this repo's own convention of `StrEnum` for every other domain enum —
`FindingType`, `Severity`, `CitationScheme`, `WorkflowState`,
`ConfidenceGrade` — plus the existing precedent in `Confidence` of pairing a
fine-grained value with a derived coarse one, `score` + `grade`).

**Rejected in favor of the lighter option below**, specifically *because* of
the `reasoner/adapter.py` call site found in §3.1: making `resolved` a
computed property removes it as a constructor keyword, which breaks that
site's `Citation(resolved=bool(...), ...)` call — a second production file
would have to change for a domain-model redesign whose actual benefit
(illegal-state prevention) is fully achievable more cheaply (below). Given
non-negotiable #2 treats every domain-model touch as its own scrutinized
change, the version with a smaller, fully-additive footprint wins here.

**Chosen:** add `Citation.checked: bool = False` (additive, default
preserves current behavior everywhere) plus one `@model_validator(mode="after")`
that makes the one actually-nonsensical combination
(`resolved=True, checked=False`) a hard `ValidationError` at construction
time — buying most of "illegal states unrepresentable" without touching
`resolved`'s field status at all. `domain/specs/__init__.py` needs **zero**
changes (it only ever reads `.resolved`, which is untouched).

```python
# domain/models/__init__.py — Citation
resolved: bool = False
checked: bool = Field(
    default=False,
    description="True iff resolve() actually checked this citation against a "
    "configured index. False means 'not checked' — resolved=False here must "
    "never be read as 'disproven', only as 'unverified'.",
)
resolution_evidence: str | None = None

@model_validator(mode="after")
def _resolved_implies_checked(self) -> "Citation":
    if self.resolved and not self.checked:
        raise ValueError("resolved=True requires checked=True (was it actually checked?)")
    return self
```

### 3.3 Fix steps

**Commit 1 — domain field + validator** (its own class-A change, per §1):
the block above, plus `model_validator` added to the existing
`from pydantic import BaseModel, Field, field_validator` import line.

**Commit 2 — the two real construction sites:**

```diff
--- a/leggie/infrastructure/citation/__init__.py
+++ b/leggie/infrastructure/citation/__init__.py
@@ async def resolve(self, citation: Citation) -> Citation:
         if self._resolution_index:
             resolved = citation.identifier in self._resolution_index
             evidence = "resolved against internal index" if resolved else "not found in index"
+            checked = True
         else:
             resolved = False
             evidence = "no resolution index configured — not independently verified"
+            checked = False

         return Citation(
             scheme=citation.scheme,
             identifier=citation.identifier,
             original_text=citation.original_text,
             resolved=resolved,
+            checked=checked,
             resolution_evidence=evidence,
         )
--- a/leggie/infrastructure/reasoner/adapter.py
+++ b/leggie/infrastructure/reasoner/adapter.py
@@
             citations.append(
                 Citation(
                     scheme=scheme,
                     identifier=identifier,
                     original_text=raw.get("original_text", identifier),
-                    resolved=bool(raw.get("resolved", False)),
+                    # Deliberative pipeline skips CoVe/Skeptic entirely (architecture
+                    # contract §3) — nothing here was checked against a configured
+                    # index, whatever the Reasoner backend's own "resolved" claims.
+                    resolved=False,
+                    checked=False,
                     resolution_evidence=raw.get("resolution_evidence"),
                 )
             )
```

(The `reasoner/adapter.py` change also **fixes a second, smaller latent
defect** the validator in Commit 1 would otherwise have caught immediately:
without it, any Reasoner payload with `"resolved": true` would now raise
`ValidationError` at construction — surfacing that this call site was
already claiming "resolved" for a citation Leggie itself never checked.
Forcing `resolved=False` here is the honest fix, not a workaround to dodge
the validator.)

**Commit 3 — the actual defect fix:**

```diff
--- a/leggie/application/services/cove_verifier.py
+++ b/leggie/application/services/cove_verifier.py
@@ async def _check_citations(self, finding: Finding) -> tuple[bool, str]:
         for cite in cites:
             resolved = await self._citation_parser.resolve(cite)
             evidence = resolved.resolution_evidence or ""
-            if not resolved.resolved and "no resolution index" not in evidence:
+            if resolved.checked and not resolved.resolved:
                 return True, f"{cite.identifier} ({evidence})"
             status = "verified" if resolved.resolved else "unverified against registry"
             notes.append(f"{cite.identifier}: {status}")
--- a/leggie/application/ports/citation_parser.py
+++ b/leggie/application/ports/citation_parser.py
@@ async def resolve(self, citation: Citation) -> Citation:
         """Resolve a citation against the available index.

-        Returns the citation with resolved=True/False + evidence.
+        Returns the citation with resolved=True/False, checked=True/False, and
+        evidence. `checked` MUST be False whenever there was no index to check
+        against. Callers (CoVeVerifier) treat resolved=False+checked=True as
+        "disproven" and resolved=False+checked=False as merely "unverified" —
+        get `checked` wrong and a citation that was never independently
+        checkable looks fabricated.
         """
```

### 3.4 Tests

New file `tests/unit/domain/test_citation_checked_contract.py` +
additions to `tests/unit/application/test_cove_verifier.py`:

1. `test_citation_resolved_without_checked_is_rejected` — the validator: `Citation(resolved=True, checked=False, ...)` raises.
2. `test_unindexed_citation_is_unverified_not_disproven` — the audit's D2
   reproducer against `GreekCitationParser(resolution_index=None)`.
3. `test_indexed_miss_is_disproven` — `GreekCitationParser` with a non-empty
   index that doesn't contain the citation → `checked=True, resolved=False` →
   `_check_citations` returns `disproven=True` (the case that must still work).
4. `test_reasoner_adapter_citations_are_never_marked_checked` — parses a
   sample Reasoner JSON payload with `"resolved": true` and asserts the
   resulting `Citation.checked is False` (and, by the validator,
   `resolved is False` too) — locks in the Commit-2 fix.

---

## 4. Defect 3 — `ModelBasedReranker` swallows its own degradation

### 4.1 Diagnosis (recap)

`ModelBasedReranker._compute_batch_scores` (`rerank.py:181-204`) catches
`except Exception:` around the call to `RerankerPort.rerank()` and falls
back to composite scoring for the whole batch with **no log call and no
event** — the only guard of its kind in the audited surface without one
(`skeptic.py:136-140` and `cove_verifier.py:254-263` both log the equivalent
failure). Confirmed reachable in the current DI graph:
`container.py:180-189` binds `RerankerPort` → `OpenRouterReranker`
whenever `LEGGIE_ANALYSIS__RERANKER=model`, wired through by
`cli_handlers.py:135-137`. `OpenRouterReranker.rerank()`
(`infrastructure/reranker.py:68-75`) raises `LLMRateLimitError` on HTTP 429
and `LLMError` on any other non-200 — both realistic in production.

### 4.2 Design decision

**Chosen:** extend `agents/lens.py`'s existing `on_degradation` +
`Event(event_type=EventType.DEGRADED, ...)` mechanism to
`ModelBasedReranker`, plus a `log.warning` (matching skeptic/CoVe's
existing standard). `EventType.DEGRADED` already exists
(`domain/models/__init__.py:106`, "LLM call failed, pipeline degraded
output") — no new enum member needed. Wiring point: `BillAnalysisFlow`
already builds `self._on_degradation` before it builds the reranker
(`bill_analysis_flow.py:86-96`); thread the same callback through
`_build_reranker()`. Both aggregation paths (`_aggregate_via_blackboard`
and the inline path) share the one `self._reranker` instance built in
`__init__`, so this is a single wiring point, not two.

**Explicitly out of scope, flagged rather than silently fixed:**
`LLMAdversarialGate` (`skeptic.py`) and `CoVeVerifier`'s LLM path
(`cove_verifier.py`) also only log on failure — they don't emit
`EventType.DEGRADED` either. Extending the Event-sourcing pattern there
would make the whole verification chain more uniformly observable, but it
wasn't part of the audited defect set, touches two more class-A files, and
"fix everything the audit found" is not license to also redesign adjacent
code the audit didn't flag. Worth a follow-up plan if the team wants full
uniformity.

**Rejected alternative — logging only (what the original audit's fix
package proposed):** brings `ModelBasedReranker` to parity with its
immediate neighbors, but the user asked for the architecture's actual
"optimal" answer here, and the codebase's own established answer to "an
LLM/port call degraded" is Event-sourcing, not logging — logging is what
the *other* components do only because they haven't been migrated to the
callback either (see previous paragraph). Since `ModelBasedReranker` has no
existing `on_degradation` wiring to preserve, there's no extra cost to
doing it the more complete way here.

### 4.3 Fix steps

```diff
--- a/leggie/application/services/rerank.py
+++ b/leggie/application/services/rerank.py
@@
 from __future__ import annotations

+import logging
 from abc import ABC, abstractmethod
+from collections.abc import Callable
 from dataclasses import dataclass
 from typing import Any

-from leggie.domain.models import Finding
+from leggie.domain.models import Event, EventType, Finding

 from leggie.application.ports.reranker import RerankerPort

+log = logging.getLogger(__name__)
+
 _SEVERITY_WEIGHTS = {
@@ class ModelBasedReranker(Reranker):
     def __init__(
         self,
         reranker_port: RerankerPort,
         query: str = "...",
         model: str = "cohere/rerank-4-pro",
         composite_fallback: CompositeReranker | None = None,
+        on_degradation: Callable[[Event], None] | None = None,
     ) -> None:
         self._port = reranker_port
         self._query = query
         self._model = model
         self._fallback = composite_fallback or CompositeReranker()
+        self._on_degradation = on_degradation
@@ async def _compute_batch_scores(self, findings: list[Finding]) -> dict[Any, float]:
         try:
             results = await self._port.rerank(
                 query=self._query,
                 documents=documents,
                 model=self._model,
             )
             return {
                 findings[r.index].id: r.relevance_score for r in results if r.index < len(findings)
             }
-        except Exception:
-            # Fall back to composite scoring
+        except Exception as e:  # noqa: BLE001 — reranker fallback must never crash the run
+            log.warning(
+                "reranker_port_failed: falling back to composite scoring for %d findings: %s",
+                len(findings), str(e)[:200],
+            )
+            self._emit_degradation(len(findings), e)
             scored = [await self._fallback.score(f, findings) for f in findings]
             return {s.finding.id: s.composite_score for s in scored}
+
+    def _emit_degradation(self, batch_size: int, exc: Exception) -> None:
+        if self._on_degradation is None:
+            return
+        try:
+            self._on_degradation(
+                Event(
+                    event_type=EventType.DEGRADED,
+                    aggregate_id="reranker:model",
+                    data={
+                        "component": "ModelBasedReranker",
+                        "batch_size": batch_size,
+                        "error": str(exc)[:500],
+                        "model": self._model,
+                    },
+                )
+            )
+        except Exception:
+            log.warning("on_degradation callback failed", exc_info=True)
--- a/leggie/application/workflow/bill_analysis_flow.py
+++ b/leggie/application/workflow/bill_analysis_flow.py
@@ def _build_reranker(
         self,
         reranker_name: str,
         reranker_port: RerankerPort | None,
     ) -> CompositeReranker | ModelBasedReranker:
         """Build the reranker requested by configuration."""
         if reranker_name == "model" and reranker_port is not None:
-            return ModelBasedReranker(reranker_port=reranker_port)
+            return ModelBasedReranker(reranker_port=reranker_port, on_degradation=self._on_degradation)
         return CompositeReranker()
```

(`_emit_degradation`'s shape deliberately mirrors `agents/lens.py:69-87`
line for line — same try/except-around-the-callback, same "never let a
broken observer crash the run" guarantee.)

### 4.4 Tests

Additions to `tests/unit/application/test_rerank.py`:

1. `test_reranker_port_failure_is_logged` — the audit's D3 reproducer
   (`caplog`, asserts `"reranker_port_failed"` appears).
2. `test_reranker_port_failure_emits_degraded_event` — a fake
   `on_degradation` collector asserts it received one
   `Event(event_type=EventType.DEGRADED, aggregate_id="reranker:model", ...)`
   with `data["batch_size"]` matching the failed batch.
3. `test_broken_on_degradation_callback_does_not_crash_rerank` — the callback
   itself raises; `rerank()` must still return composite-scored results
   (proves the inner try/except).
4. `test_bill_analysis_flow_wires_on_degradation_into_model_reranker` — one
   integration-shaped test constructing `BillAnalysisFlow(reranker_name="model",
   reranker_port=<failing fake>, ...)` and asserting a `DEGRADED` event
   lands in `flow.get_event_log()` after a run — proves the wiring, not just
   the class in isolation.

---

## 5. What this plan deliberately does NOT do

- **No regex removal.** `article_number()` / the `Άρθρο\s+(\d+)` pattern
  stays as the fallback for every `Finding` that predates `article_id`
  (including anything sitting in an existing `CheckpointStore` file or a
  previously-saved `findings.json` re-loaded some other way). Removing it
  would be the "Contract" phase of expand-contract and has no forcing
  function here — there's no migration deadline, so leave it as a permanent,
  cheap safety net rather than a temporary shim to later delete.
- **No change to `CitationParserPort` or `RerankerPort` method signatures**
  — confirmed against non-negotiable #3 in §0's table.
- **No uniform Event-sourcing rollout across skeptic/CoVe's own LLM-failure
  paths** — flagged in §4.2 as a legitimate follow-up, not bundled in.
- **No touching `pyproject.toml`'s ruff ignore list** — none of these fixes
  need it; if implementation surfaces a new lint finding, fix the code
  (non-negotiable #5), don't widen the list.

---

## 6. Commit sequence

Ordered, each independently gated (§7) before the next starts:

1. `feat(domain): add Finding.article_id (D1a)` — domain-only, additive.
2. `feat(domain): add Citation.checked + resolved-implies-checked validator (D2a)` — domain-only, additive.
3. `fix(lenses): populate Finding.article_id at construction (D1b)` — all 5 lens files.
4. `fix(verification): resolve article text via article_id, consolidate the regex fallback (D1c)` — `cove_verifier.py`, `blackboard_aggregator.py`, `bill_analysis_flow.py`.
5. `fix(citation): set Citation.checked at both construction sites (D2b)` — `infrastructure/citation/__init__.py`, `infrastructure/reasoner/adapter.py`.
6. `fix(verification): use Citation.checked instead of string-matching resolution_evidence (D2c)` — `cove_verifier.py`, `ports/citation_parser.py` docstring.
7. `fix(rerank): surface ModelBasedReranker port failures as logged + DEGRADED events (D3)` — `rerank.py`, `bill_analysis_flow.py`.

Each commit references its defect ID (D1a, D1b, …) per
`leggie-change-control` §4 commit conventions.

---

## 7. Gates (per `leggie-change-control` §2, run after every commit above)

```
python -m pytest tests/ -q            # baseline going in: 546 passed, 1 skipped (post PR #10)
mypy leggie/ --ignore-missing-imports
ruff check leggie/ tests/ scripts/
ruff format --check leggie/ tests/ scripts/
lint-imports
bandit -c pyproject.toml -r leggie/
```

Or simply `python scripts/run_gates.py` (runs all six in this order,
already CI-equivalent per the local-gates infrastructure this branch added).

**Actual result on the full combined change:** `python scripts/run_gates.py`
— all 6 gates PASS. `567 passed, 1 skipped` (+21 tests over the 546 baseline:
7 new test functions/classes plus edits to 7 pre-existing `Citation`
constructions that needed `checked=True` added once `resolved=True` implied
it — see §11). Coverage 83.12%, above the 80% floor. `ruff-format` needed one
mechanical `ruff format` pass after the edits (line-wrapping only, no
semantic change) before it passed.

---

## 8. Residual risk — live smoke not run

All three defects are class-A (§0 header). Per `leggie-change-control` §2,
that requires a live smoke judged against `docs/REMEDIATION_PLAN.md` §10 —
**not run for this plan**, same constraint as `PLAN_VERIFICATION_CHAIN_ORDER.md`
§5: no `LEGGIE_LLM__OPENROUTER_API_KEY` in this environment, and a smoke run
is billable.

What the offline gates + regression tests in §2.4/§3.4/§4.4 do and don't establish:

- **Established (once implemented and gated green):** the three mechanisms
  are fixed and each regression test fails on the pre-fix code and passes
  after, per this repo's standing verification discipline.
- **Not established:** how often D1's actual trigger condition (an LLM
  `issue` string without "Άρθρο N") occurs on real bills, and therefore how
  many real findings were silently skipping F3 in production before this
  fix. That requires a live run.

Recommended live-smoke check when a key is available (procedure:
**leggie-run-and-operate**; measurement: **leggie-diagnostics-and-tooling**):

1. Run `leggie analyze` on a real bill with `LEGGIE_ANALYSIS__RERANKER=model`
   set, before and after this plan is implemented.
2. Grep the "before" run's `findings.json` / logs for how many findings had
   an unresolvable article number under the old regex-only path — this
   directly measures D1's real-world blast radius.
3. Confirm zero `reranker_port_failed` / `DEGRADED` events on a healthy run,
   and force one (bad API key) to confirm the event fires end-to-end (D3).

---

## 9. Rollback

Each commit in §6 is independently revertible (`git revert <sha>`) without
touching the others — that's the point of keeping domain-field additions
separate from their consumers. If a later commit in the sequence needs to be
reverted, the earlier additive domain fields are harmless to leave in place
(unused fields default to `""` / `False`, no behavior change).

Monitoring signals to instrument alongside this rollout:
`article_index lookup miss` counter in `CoVeVerifier.verify_batch` (should
trend to ~0), `reranker_port_failed` log/event rate (should stay near 0;
sustained nonzero means the "model" reranker config is quietly degrading).

---

## 10. Traceability

- Findings D1–D3: static defect audit performed in-thread against this
  branch's head, scoped to the verification-chain surface.
- Precedent plan format: `docs/PLAN_VERIFICATION_CHAIN_ORDER.md` (PR #10).
- Architecture invariants cited: `.claude/skills/leggie-architecture-contract/SKILL.md` §1, §4, §5.
- Change-control rules cited: `.claude/skills/leggie-change-control/SKILL.md` §1–§3.

---

## 11. Implementation report

Implemented as designed in §2–§4, with two deviations from the original
plan text, both discovered by running the actual test suite rather than by
further static reading — exactly the kind of thing this plan couldn't
predict from inspection alone:

1. **§2.3/§3.3 diffs applied exactly as written** for every file this plan
   had already read in full. The 4 lens files flagged as "pattern-matched,
   not individually opened" (§2.3 Commit 2) were opened before editing, as
   the plan required; all matched the assumed shape (`Finding(...)` at the
   same relative position, same `article_id=article.id,` one-line addition).
   No surprises there.

2. **§3.2's validator had a wider blast radius than the design discussion
   anticipated.** Adding `Citation`'s `resolved`-implies-`checked` validator
   immediately broke 7 pre-existing `Citation(resolved=True, ...)`
   constructions across `tests/unit/domain/test_models.py`,
   `tests/unit/domain/test_specs.py`, and `tests/unit/application/test_cove_verifier.py`
   that had no way to know about a field this plan hadn't written yet. Each
   was a genuinely-resolved citation in its test's own scenario, so the fix
   was to add `checked=True` at each site — not to weaken the validator.
   This is exactly the failure mode §3.2 argued the validator should catch;
   it caught it in the test suite instead of in production, which is the
   point of writing it. Recorded here rather than silently folded into "the
   plan was followed" because it changed the size of Commit 1 in §6 from
   "2 files" to "2 files + 3 test files."

3. **§4.3's wiring test (`test_reranker_degradation_reaches_flow_event_log`)
   uncovered nothing wrong** — `BillAnalysisFlow._build_reranker` threading
   `self._on_degradation` through was correct on the first attempt, verified
   by the fail-old/pass-new stash proof in the same way as every other test
   here.

**Fail-old/pass-new proof method used:** `git stash push -m "<label>" -- <source files>`
(keeping every test file un-stashed), run only the tests naming that defect,
confirm they fail (ImportError for D1's `article_number_of`-dependent tests,
`AttributeError`/`AssertionError` for the rest), `git stash pop`, confirm
`git status` shows all 20 touched files back. Done once per defect (D1, D2,
D3) rather than once per commit in §6, since the commits within one defect
share the same mechanism and splitting the proof further wouldn't have
added information — the sequencing in §6 is about review granularity and
revertibility, not about needing a separate regression proof per commit.

**Not done:** the live smoke in §8 — still blocked on the same missing
`LEGGIE_LLM__OPENROUTER_API_KEY` as `PLAN_VERIFICATION_CHAIN_ORDER.md` §5.

**§6's commit sequence was grouped by defect instead of by sub-letter.**
`domain/models/__init__.py` carries both D1a and D2a (the `Finding` and
`Citation` edits are in different classes in the same file); `cove_verifier.py`
carries both D1c and D2c; `bill_analysis_flow.py` carries both D1c and D3.
Splitting these at the sub-letter granularity §6 specified would need
hunk-level (`git add -p`) staging for no real reviewability gain — a
sub-lettered commit like "D1a only" is inert on its own anyway (a domain
field with no populating lens and no consuming call site does nothing), so
the meaningful revert boundary is the defect as a whole, not the sub-step.
Committed as one commit per defect (D1, D2, D3) plus this doc, each
containing its domain change, its consumers, and its tests together —
still separate commits, still each independently revertible, just at the
granularity that's actually meaningful to revert. `git add -p` was used to
split shared files (`domain/models/__init__.py`, `cove_verifier.py`,
`bill_analysis_flow.py`) by hunk so each commit's diff is exactly its own
defect's lines — verified by inspecting `git diff --cached` before each
commit. One exception: `tests/unit/application/test_bill_analysis_flow.py`
was staged whole for the D1 commit (`c28e350`) before its D3 test
(`test_reranker_degradation_reaches_flow_event_log`) was split out, so that
one test rides in the D1 commit rather than the D3 commit (`84ff62d`) —
cosmetic, not a correctness issue, noted here rather than rewriting
already-made history for it.
