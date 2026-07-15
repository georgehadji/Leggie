---
name: leggie-docs-and-writing
description: >
  How Leggie's documents of record are organized, written, and maintained.
  Load when writing or updating any plan doc, audit report, README section, or
  commit message for this repo. Provides the docs inventory with status, the
  vision→plan→implement→audit lifecycle, embedded templates for fix-plans and
  audit reports extracted from the real documents, house style rules, and the
  known documentation drift list.
---

# Leggie Docs and Writing

## 1. Documents of record (inventory, status 2026-07-10)

| Doc | Role | Status |
|---|---|---|
| `README.md` | public face; quick start, architecture summary | LIVING — has known drift (§5) |
| `docs/Leggie_Initial.md` | original vision (25 personas, debate, KG…) | HISTORICAL — superseded by tasks/todo.md descoping |
| `tasks/todo.md` | reviewed/optimized v1 plan; the descoping table ("what changed vs initial spec") | HISTORICAL/reference — decisions still binding |
| `docs/BUILD_PLAN.md` | build phases | HISTORICAL |
| `docs/ARCHITECTURE.md` | architecture description | LIVING reference (verify against source; **leggie-architecture-contract** is ground truth) |
| `docs/ARCH_UPGRADE_PLAN.md` | G1–G10 upgrade plan (8/10 → 9.5) | MOSTLY EXECUTED (G1 open) |
| `ARCH-AUDIT-V2.md` (root) | formal architecture audit, score 8/10 | HISTORICAL audit |
| `docs/WIRING_PLAN.md` | wiring work plan (W-items) | HISTORICAL |
| `docs/FIX_PLAN.md` | stub-MVP diagnosis + F0–F5 fixes | HISTORICAL — the canonical honest-diagnosis exemplar |
| `docs/REMEDIATION_PLAN.md` | LIVE defect inventory D1–D10 + phases + §10 DoD | **ACTIVE** — the current plan of record |
| `implementation_audit_report.md` (root) | Phase-1 audit (verdict, H/M findings) | ACTIVE until H-items closed |
| `implementation_plan.md`, `ARCHITECTURE_MINDMAP.md`, `analysis_report.md` (root) | working artifacts / historical exhibits | HISTORICAL |

Observed convention (describe, don't fight): **plans live in `docs/`; audits
and run exhibits land at repo root.** `tasks/todo.md` is the planning
scratchpad location.

## 2. The document lifecycle (as practiced here)

```
VISION (Leggie_Initial.md)
  → REVIEWED PLAN with explicit descoping table (tasks/todo.md §0 "What changed vs the initial spec" + why)
    → DEFECT-INVENTORY FIX PLAN (FIX_PLAN.md, REMEDIATION_PLAN.md)
      → IMPLEMENTATION (commits reference plan item IDs)
        → AUDIT REPORT (implementation_audit_report.md: compliance matrix, severity findings, verdict)
          → next plan iterates on audit findings
```

Every major change traces to a plan; every executed phase gets audited.
IDs are permanent handles: D1..D10 (remediation defects), F0..F5 (fix-plan
items), G1..G10 (arch upgrades), W-items (wiring), H-/M- (audit findings by
severity). Never renumber; new items get new IDs.

## 3. Templates (extracted from the real docs)

### Fix-plan template (pattern: REMEDIATION_PLAN.md)

```markdown
# <Name> Plan
**Date:** YYYY-MM-DD  **Branch:** <branch>  **Author:** <context>

## 0. Current state (what already works — do not re-touch)
## 1. Defect inventory (ranked by yield impact)
| # | Defect | Layer | Evidence | Severity |
|---|--------|-------|----------|----------|
| D1 | <one-line mechanism> | Domain/App/Infra | file:line or log signature + count | CRITICAL/HIGH/MEDIUM/LOW |

## 2..N. Phase <k> — <goal>  (one section per phase)
   - exact changes, file paths, layer named
   - **Tests:** what proves the phase
## N+1. Execution order & dependencies (ASCII graph)
## N+2. Architecture guardrails (apply to every phase)
## N+3. Definition of done — MEASURABLE numbers only
```

### Audit-report template (pattern: implementation_audit_report.md)

```markdown
# Implementation Audit — <scope>
**Audit date:** …  **Scope:** <plan §>  **Commit:** <sha>  **Test baseline:** N passed

## 1. Executive Summary
**Verdict: APPROVED | APPROVED WITH CHANGES | REJECTED** (n HIGH, m MEDIUM)
## 2. Plan Compliance Matrix
| Plan Item | Status COMPLETE/PARTIAL/MISSING | Evidence | Notes |
## 3. Architecture Compliance (dependency rule, ports, immutability, no-silent-failure)
## 4. Findings — H-1, H-2… (HIGH), M-1… (MEDIUM), each: mechanism, evidence, required fix
```

### Commit messages (verified from git log)

`<type>[(scope)]: <description>` — types seen: feat, fix, config; scope often
a phase (`feat(phase1):`); bodies state ground truth measured before/after
("mypy … now reports zero errors (was 91-108)"). Reference plan IDs.

## 4. House style (extracted, binding)

1. **Evidence with every claim** — file:line, commit sha, or counted log
   signature. "Parser is broken" is not a finding;
   "`parse/__init__.py:17-20` matches in-body cross-references, producing
   fake article 552" is.
2. **Brutal honesty** — the project called its own MVP output "noise" in
   writing (FIX_PLAN header). Preserve that register; no marketing language
   in internal docs.
3. **Measurable DoD** — a plan without numbers in its definition-of-done is
   not done being written.
4. **Status markers** — COMPLETE / PARTIAL / OPEN / SETTLED on every tracked item.
5. **Date-stamp volatile facts** and name the branch/commit context.
6. **"Do not re-touch" section** — plans open by fencing what already works.

## 5. Known documentation drift (record, fix when touching README anyway)

| Claim | Reality (2026-07-10) |
|---|---|
| README badge "tests-199 passed" | 531 passing (2026-07-15) |
| README badge "code-5,195 lines" | ~7,850 lines in leggie/ (79 files) |
| README "7 abstract interfaces (ports)" | 10 port classes in `application/ports/` |
| `.env.example` `MAX_TOKENS_PER_RUN=500000` | code default 20,000,000 (500k was the budget-block bug) |
| README roadmap/phases | check against REMEDIATION_PLAN before citing |

## When NOT to use this skill

- Whether a change NEEDS a plan/audit → **leggie-change-control**
- Content of past investigations → **leggie-failure-archaeology**
- Architecture facts to cite → **leggie-architecture-contract**
- External claims/papers → **leggie-research-frontier** (positioning section)

## Provenance and maintenance

Dated 2026-07-10. Re-verify:
- Inventory: `ls docs tasks *.md`
- Drift — tests: `python -m pytest tests/ -q`; lines: `find leggie -name "*.py" -exec wc -l {} + | tail -1` (Git Bash); ports: `ls leggie/application/ports/`
- Commit style: `git log --format="%s" -15`
- Active plan: `head -20 docs/REMEDIATION_PLAN.md`
