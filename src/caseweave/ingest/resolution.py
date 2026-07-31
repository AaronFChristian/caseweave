"""Entity resolution.

Deterministic blocking + scoring. No ML, on purpose: in a regulated
investigation an examiner will ask why two identities were merged, and
"a model said 0.87" is a worse answer than "same DOB, same surname, same
registered address".

Blocking keys, in descending confidence:
  dob_name  — identical DOB and surname
  device    — same device fingerprint
  address   — same registered address

Only dob_name promotes a canonical_id (an actual merge). The weaker signals
are recorded as links for the network-analysis agent to traverse on Day 2 —
sharing a household is a lead, not an identity.
"""

from __future__ import annotations

from collections import defaultdict

from caseweave.models import EntityLink, Party

MERGE_METHODS = {"dob_name"}
METHOD_SCORE = {"dob_name": 0.95, "device": 0.60, "address": 0.40}


def _norm_surname(p: Party) -> str | None:
    if p.last_name:
        return p.last_name.strip().lower()
    return None


def resolve(parties: list[Party]) -> tuple[list[Party], list[EntityLink]]:
    """Return parties with canonical_id populated, plus the link set."""
    blocks: dict[str, dict[str, list[str]]] = {
        "dob_name": defaultdict(list),
        "device": defaultdict(list),
        "address": defaultdict(list),
    }

    for p in parties:
        sn = _norm_surname(p)
        if p.dob and sn:
            blocks["dob_name"][f"{p.dob.isoformat()}|{sn}"].append(p.party_id)
        if p.device_id:
            blocks["device"][p.device_id].append(p.party_id)
        if p.address_id:
            blocks["address"][p.address_id].append(p.party_id)

    links: list[EntityLink] = []
    n = 0
    for method, groups in blocks.items():
        for _key, members in groups.items():
            if len(members) < 2 or len(members) > 12:
                continue  # oversized blocks are noise, not signal
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    n += 1
                    links.append(
                        EntityLink(
                            link_id=f"L{n:06d}",
                            party_id_a=members[i],
                            party_id_b=members[j],
                            method=method,
                            score=METHOD_SCORE[method],
                        )
                    )

    # Union-find over merge-grade links only.
    parent: dict[str, str] = {p.party_id: p.party_id for p in parties}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)  # lowest id wins, deterministic

    for lk in links:
        if lk.method in MERGE_METHODS:
            union(lk.party_id_a, lk.party_id_b)

    resolved = [p.model_copy(update={"canonical_id": find(p.party_id)}) for p in parties]
    return resolved, links


def merge_stats(parties: list[Party]) -> dict[str, int]:
    groups = defaultdict(list)
    for p in parties:
        groups[p.canonical_id].append(p.party_id)
    merged = {k: v for k, v in groups.items() if len(v) > 1}
    return {
        "canonical_entities": len(groups),
        "merged_clusters": len(merged),
        "records_merged_away": sum(len(v) - 1 for v in merged.values()),
    }
