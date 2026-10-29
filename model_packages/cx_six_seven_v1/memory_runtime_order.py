"""Package-local formal ordering; ordinals never replace physical age."""
from collections.abc import Mapping

RUNTIME_MEMORY_ORDER_VERSION = "cx67-memory-runtime-order-v1"


def ordinal(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("memory accepted runtime ordinal must be a positive integer")
    return value


def validate_node_ordinals(times, ordinals):
    if not isinstance(ordinals, Mapping) or set(ordinals) != set(times):
        raise ValueError("memory runtime order must cover exact canonical nodes")
    result = {key: ordinal(value) for key, value in ordinals.items()}
    if len(set(result.values())) != len(result):
        raise ValueError("memory runtime ordinals must be unique")
    ordered = sorted(result, key=result.get)
    if any(times[left] > times[right] for left, right in zip(ordered, ordered[1:])):
        raise ValueError("memory runtime order reverses physical time")
    return result


def readout_order(receipt):
    record = receipt.get("runtime_order")
    if record is None:
        return None
    if not isinstance(record, Mapping) or set(record) != {"version", "accepted_runtime_ordinal"}:
        raise ValueError("memory readout runtime order fields are invalid")
    if record["version"] != RUNTIME_MEMORY_ORDER_VERSION:
        raise ValueError("memory readout runtime order version is invalid")
    return ordinal(record["accepted_runtime_ordinal"])


def order_record(value):
    return {"version": RUNTIME_MEMORY_ORDER_VERSION, "accepted_runtime_ordinal": ordinal(value)}
