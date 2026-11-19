from types import SimpleNamespace

from product_adapters.desktop_pet_v1.retired_writer import retire_stale


def test_retire_only_reads_attempts_without_a_final_receipt():
    ids = ("committed", "failed", "other-pending", "same-input")
    receipts = [dict(stage="allocated", writer_request_id=key) for key in ids]
    receipts.extend([dict(stage="commit", writer_request_id=ids[0]),
                     dict(stage="terminal", terminal_status="failed", writer_request_id=ids[1]),
                     dict(stage="terminal", terminal_status="candidate_ready", writer_request_id=ids[2])])
    reads = []
    def read_attempt(key):
        reads.append(key)
        return {"source_evidence": {"event_id": "other"}}
    journal = SimpleNamespace(read_receipts=lambda:receipts, read_attempt=read_attempt)
    headers = []
    def source(row):
        headers.append(row["writer_request_id"])
        return "new" if row["writer_request_id"] == "same-input" else "other"
    retire_stale(SimpleNamespace(application=object(),attempt_journal=journal,_attempt_source=source), "new")
    assert headers == ["other-pending", "same-input"]
    assert reads == ["same-input"]
