"""Exact source-declared addresses, progressively coarser only within an owner.

No embeddings, aliases, business catalogue, persistence or model invocation.
These hashes are local read metadata, never invented business identifiers.
"""
from framework.model_api import model_data_fingerprint, thaw_model_data

VERSION = "package-hierarchical-semantic-address-v1"


def levels(address):
    row = thaw_model_data(address)
    if set(row) != {"slot_id", "entity_id", "attribute"}:
        raise ValueError("address fields differ")
    if row["entity_id"] is None:
        return []
    if any(not isinstance(row[key], str) or not row[key].strip()
           for key in ("slot_id", "entity_id", "attribute")):
        raise ValueError("explicit address components required")
    parts = row["attribute"].split("/")
    if any(not part for part in parts):
        parts = [row["attribute"]]  # Ambiguous hierarchy remains a literal.
    return [model_data_fingerprint({"version": VERSION, "slot_id": row["slot_id"],
        "entity_id": row["entity_id"], "path": parts[:n]}) for n in range(len(parts), -1, -1)]


def address_indexes(addresses):
    result = []
    for row in addresses:
        for key in levels(row):
            if key not in result:
                result.append(key)
    return result


def match_addresses(queries, candidates):
    rows = thaw_model_data(candidates)
    ids = [r["event_id"] for r in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("candidate addresses repeat an event")
    selected, decisions = set(), []
    for query in queries:
        decision = {"query": thaw_model_data(query), "level": None, "fingerprint": None, "matches": []}
        for depth, key in enumerate(levels(query)):
            matches = [r["event_id"] for r in rows if key in r["indexes"]]
            if matches:
                decision.update(level=depth, fingerprint=key, matches=matches)
                selected.update(matches)
                break
        decisions.append(decision)
    return [i for i in ids if i in selected], decisions
