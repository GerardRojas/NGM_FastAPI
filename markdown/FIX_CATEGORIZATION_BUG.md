# Bug Fix: Andrew Agent Categorization Error

## Error
```
Agent processing error: string indices must be integers, not 'str'
```

## Root Cause
The `agent_process_receipt` function (line ~1454) is calling `_auto_categorize_core` but expecting it to return a list directly. However, after the recent commit `c64e847`, this function now returns a dictionary with the format:

```python
{
  "categorizations": [...],
  "metrics": {...}
}
```

## Current Buggy Code (line ~1454)
```python
categorizations = []
try:
    if cat_expenses:
        categorizations = _auto_categorize_core(stage=construction_stage, expenses=cat_expenses)
except Exception as cat_err:
    print(f"[Agent] Auto-categorization failed: {cat_err}")
```

This makes `categorizations` a **dict** instead of a **list**. Then when the code does:

```python
cat_map = {c["rowIndex"]: c for c in categorizations}
```

It iterates over the dict keys ("categorizations", "metrics"), and tries `c["rowIndex"]` on a **string**, causing the error.

## Fix Required

Replace lines 1451-1456 with:

```python
categorizations = []
cat_metrics = {}
try:
    if cat_expenses:
        cat_result = _auto_categorize_core(
            stage=construction_stage,
            expenses=cat_expenses,
            project_id=project_id,
            receipt_id=receipt_id
        )
        categorizations = cat_result.get("categorizations", [])
        cat_metrics = cat_result.get("metrics", {})
except Exception as cat_err:
    print(f"[Agent] Auto-categorization failed: {cat_err}")
```

## Location
File: `api/routers/pending_receipts.py`
Lines: ~1451-1456

## Same Pattern Fixed Elsewhere
This exact same fix was already applied in the `process_receipt` function around line 432 (see the diff from commit c64e847).
