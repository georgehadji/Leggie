"""Pure clustering/dedup functions — group near-duplicate findings.

All functions are pure: no I/O, injected similarity function.
"""

from __future__ import annotations

from collections.abc import Callable

from leggie.domain.models import Finding


def cluster(
    findings: list[Finding],
    similarity_fn: Callable[[Finding, Finding], float],
    threshold: float = 0.85,
) -> list[list[Finding]]:
    """Group findings into clusters by semantic similarity.

    Simple greedy clustering: for each unclustered finding, group with all
    sufficiently similar unclustered findings. O(n²) — fine for typical bill
    output sizes (<500 findings).
    """
    if not findings:
        return []

    remaining = list(findings)
    clusters: list[list[Finding]] = []

    while remaining:
        pivot = remaining.pop(0)
        cluster_group = [pivot]
        still_remaining: list[Finding] = []
        for f in remaining:
            if similarity_fn(pivot, f) >= threshold:
                cluster_group.append(f)
            else:
                still_remaining.append(f)
        clusters.append(cluster_group)
        remaining = still_remaining

    return clusters


def deduplicate(
    findings: list[Finding],
    similarity_fn: Callable[[Finding, Finding], float],
    threshold: float = 0.90,
    keep: str = "highest_confidence",
) -> list[Finding]:
    """Remove near-duplicate findings, keeping the best representative per cluster.

    Args:
        findings: Input findings list.
        similarity_fn: Function (Finding, Finding) -> float similarity.
        threshold: Similarity threshold for considering duplicates.
        keep: Strategy — 'highest_confidence', 'most_severe', 'first'.

    Returns:
        Deduplicated list of findings.
    """
    if not findings:
        return []

    clusters_list = cluster(findings, similarity_fn, threshold)
    result: list[Finding] = []

    for cluster_group in clusters_list:
        if len(cluster_group) == 1:
            result.append(cluster_group[0])
            continue

        if keep == "highest_confidence":
            chosen = max(cluster_group, key=lambda f: (f.confidence.score, str(f.id)))
        elif keep == "most_severe":
            severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
            chosen = min(cluster_group, key=lambda f: (
                severity_order.get(f.severity.value, 5), str(f.id)))
        else:
            chosen = cluster_group[0]
        result.append(chosen)

    return result


def merge_findings(
    findings_a: list[Finding],
    findings_b: list[Finding],
    similarity_fn: Callable[[Finding, Finding], float],
    threshold: float = 0.85,
) -> list[Finding]:
    """Merge two lists of findings, deduplicating across both."""
    combined = findings_a + findings_b
    return deduplicate(combined, similarity_fn, threshold)
