# Where Leggie Should Improve — Evidence-Led Assessment

**Date:** 2026-07-28
**Method:** measured against the real input (`Inputs/OE_ΣΧΝ-ΥΠΔΙΚ.pdf`) and the
three worktree runs (`full5_v3/v4/v5`), not against the code's intentions.

---

## 0. Headline: the pipeline has been analyzing the table of contents

Every live run in this campaign — `full5_v3`, `full5_v4`, `full5_v5`, and by
extension the `subset*` runs — analyzed **two records, both of which are
table-of-contents fragments.** Not legal articles.

`--articles 1-10` was requested. Here is what the analyze path actually fed to
the lenses:

```
=== "ARTICLE 1" ===
Άρθρο 1 Σκοπός
Άρθρο 2 Αντικείμενο (άρθρο 1 της Οδηγίας (ΕΕ) 2024/1069)
Άρθρο 3 Πεδίο εφαρμογής (άρθρα 2, 3 και 5 της Οδηγίας (ΕΕ) 2024/1069)
Άρθρο 4 Ορισμοί (άρθρο 4 της Οδηγίας (ΕΕ) 2024/1069)
ΚΕΦΑΛΑΙΟ Β΄ ...

=== "ARTICLE 6" ===
Άρθρο 6 Αναγνώριση αγωγής ως καταχρηστικής
Άρθρο 7 Βάρος απόδειξης (άρθρο 12 της Οδηγίας (ΕΕ) 2024/1069)
Άρθρο 8 Εγγυοδοσία για τα δικαστικά έξοδα ...
```

These are TOC listings. The bill's actual Article 1 text was never analyzed.

### Why this went unnoticed

The findings *looked* plausible, and the verification layers debated them
seriously:

| Run | Finding produced | What it actually is |
|---|---|---|
| v3 | «Ασαφής δομή και αρίθμηση άρθρων» | the LLM correctly observing that its input is a malformed heading list |
| v5 | «Ελλιπής και ασαφής δομή της τιτλοφόρησης και αρίθμησης των άρθρων» | same |
| v3 | skeptic: «νομοτεχνική ασυνέπεια στη μορφοποίηση της επικεφαλίδας» | the critic arguing about a parser artifact |
| v3/v4/v5 | economic findings re «Εγγυοδοσία / δικαστικά έξοδα» | derived from a **TOC line**, not the article body |

The system has been generating findings about its own parser bug, and the
adversarial critic has been rigorously adjudicating them. Every quality metric
in `SMOKE_AUDIT_V3.md` was computed over this.

---

## 1. The parser defect, quantified

Parsed with the exact call `bill_analysis_flow._do_parse` makes
(`parse(text, title=stem, source_format="pdf")`):

| Measure | Value | Expected |
|---|---|---|
| Article records emitted | **121** | 91 |
| Distinct IDs | **78** | 91 |
| IDs emitted more than once | **43** | 0 |
| Article numbers missing entirely | **13** — 2,3,4,5,7,8,9,10,13,19,38,60,77 | 0 |
| Records under 200 chars | **50 of 121** | few |
| Records matching `--articles 1-10` | **2** (`'1'`, `'6'`) | 10 |

The duplicates are TOC-line/body pairs — the parser emits the heading list
entry *and* the real article under the same ID:

```
id=41  len=161   "Άρθρο 41 Διοικητικών Δικαστηρίων – Τροποποίηση παρ. 3 …"   ← TOC line
id=41  len=999   "Άρθρο 41\nΠροσθήκη υπηρεσιακών συμβουλίων …"               ← real article
```

So the parser has three distinct faults: it **captures the TOC as articles**,
**duplicates** the 43 articles that also appear in the TOC, and **loses 13**
articles outright.

### Consequences that invalidate current numbers

1. **Yield is not what the audit says.** «0.30 findings/article» is 3 findings ÷
   10 *requested* articles. Two ran. The gate «≥0.14 findings/article» has been
   measured against a denominator that was never analyzed.
2. **The full-bill cost forecast (~$5.01) is unfounded.** It extrapolates from
   runs that processed 2 records at ~$0.07. A genuine 91-article, 5-lens run is
   a different order of magnitude, and the $5 cap decision rests on it.
3. **CoVe's `verified=0/3` in every run** — «δεν παρασχέθηκε το κείμενο-πηγή» —
   is consistent with `article_index` being keyed off these malformed records.
   It was read as CoVe being conservative; it is more likely CoVe being unable
   to find source text that was never there.

---

## 2. What this means about the campaign

The remediation campaign was rigorous about the wrong layer. D11/D12/D18/D21,
the retry ladder, route ceilings, clamping — all real, all correctly fixed and
evidenced. But they are **LLM plumbing**. The measured outcome they were
steering (parse-failure %) is a property of JSON handling, not of whether Leggie
finds real legal problems.

Meanwhile the document under analysis was wrong, and no gate could detect it,
because every §10 gate is a plumbing gate: parse failures, truncations, LLM
errors, spend, wall-clock. Not one asks *"is the analyzed text actually the
bill?"*

---

## 3. Prioritized improvements

### P0 — Fix the parser; re-baseline everything

Nothing downstream means anything until this lands.

- Detect and exclude the table of contents before article segmentation (the TOC
  is structurally identifiable: consecutive `Άρθρο N <title>` lines with no body).
- Make article IDs unique; on collision keep the record with a body, not the
  heading.
- Assert completeness: parsed article numbers must be contiguous 1..N with no
  gaps, and N must match the highest number seen. Fail loudly otherwise.
- **Re-run v3/v4/v5 after the fix.** Every number in `SMOKE_AUDIT_V3.md` §2/§3/
  §3a/§3b becomes historical once the input changes.

### P0 — Add a parse-integrity gate to §10

The campaign's gate list cannot currently fail on "we analyzed the wrong text".
Add, as hard gates:

- article count parsed == article count expected (contiguity check above)
- zero duplicate IDs
- zero zero-length or heading-only article bodies
- the selection expression resolves to the number of articles requested
  (`--articles 1-10` → 10, or an explicit error)

The last one alone would have caught this on day one.

### P1 — The gold set is 2 entries

`tests/eval/gold_set_sample.json` has **2 labelled findings**. Precision/recall/
F1/RDI computed over n=2 cannot distinguish a working analyzer from a broken
one. This is why a TOC-analyzing pipeline still "passed".

Build a real gold set (target 50–100 labelled findings over ≥2 bills) before
tuning anything for quality. Until then, no claim about analytical quality is
measurable — only plumbing is.

### P1 — Findings-per-article is the wrong headline metric

Yield says nothing about correctness; a lens emitting plausible-sounding noise
scores identically to one finding real defects. Once the gold set exists,
replace the yield gate with precision/recall against it, and keep yield only as
a sanity floor.

### P2 — Re-derive the cost model

After the parser fix, measure cost on a genuine 10-article run, then forecast
91. The current $5 cap and the "full run costs ~$5.01, would abort" conclusion
are both built on 2-record runs.

### P2 — Verification layers deserve re-evaluation on real input

The skeptic and CoVe have only ever been exercised against TOC-derived findings.
Their observed behaviour — the verdict spread, the `verified=0/N`, the
retired D17 diversity row — was all measured on garbage input. Re-characterize
them on real articles before concluding anything about their calibration.

### P3 — Two parse surfaces should not disagree

`leggie parse` writes 91 clean sequential IDs (ID-only, no bodies);
`leggie analyze` builds 121 records with duplicates. Whatever the intent, the
preview a user inspects should be the document that gets analyzed. Make them
share one code path and one output shape.

---

## 4. What is genuinely solid and should be kept

Not everything here is bad news — the engineering discipline is real:

- **The retry ladder and its instrumentation.** D15/D16's envelope-vs-content
  distinction and the `structured_response_exhausted` diagnostic are what made
  this investigation tractable at all.
- **No-silent-failure discipline.** CoVe parse failures degrade to
  `consistency=unknown dropped=False` rather than dropping findings; D13's
  skeptic degradation events; the `lens_degraded` events that first pointed at
  articles 1 and 6.
- **D20's honesty.** Catching that every prior measurement ran the wrong
  checkout, and retracting the D18 claim rather than keeping it, is the reason
  this analysis could trust the v3/v4/v5 logs at all.
- **Clean architecture boundaries.** The layer contract held throughout; the D21
  fix was a 3-file change with no port churn.

The method is sound. It was pointed at the wrong layer.

---

## 5. Suggested order of work

```
1. Parser fix + parse-integrity gates        (P0, offline, testable, free)
2. Re-run full5 on genuinely 10 real articles (~$0.10) → new baseline
3. Gold set expansion                        (P1, offline, the real unlock)
4. Re-characterize skeptic/CoVe on real text (P2)
5. Re-derive cost model, then decide on N=91 (P2)
```

Steps 1 and 3 are free and offline. Everything expensive should wait behind
them — the last three paid runs measured a table of contents.
