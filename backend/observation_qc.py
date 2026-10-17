"""QC labels describe received values; they never change availability."""
import csv

STATUSES = ("none", "failed", "passed")
QC_COLUMNS = [f"qc_{status}" for status in STATUSES] + [f"qc_{status}_percent" for status in STATUSES]


def normalize_qc(value):
    status = str(value or "none").strip().lower() or "none"
    if status not in STATUSES:
        raise ValueError(f"Unknown qc_status {value!r}; use none, passed, or failed")
    return status


def csv_delimiter(header):
    try:
        return csv.Sniffer().sniff(header, delimiters=",;\t").delimiter
    except csv.Error:
        return ","


def qc_fields(expected, available, counts=None):
    counts = counts or {}
    failed, passed = (int(counts.get(f"qc_{status}", 0)) for status in ("failed", "passed"))
    # Older reports without a QC column remain available, but unclassified.
    none = int(counts.get("qc_none", available - failed - passed))
    result = dict(qc_none=none, qc_failed=failed, qc_passed=passed)
    result.update({f"qc_{status}_percent": round(100 * result[f"qc_{status}"] / available, 2) if available else None
                   for status in STATUSES})
    return result
