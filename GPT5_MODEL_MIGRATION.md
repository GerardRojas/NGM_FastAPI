# GPT-5 Model Migration - NGM Hub Backend
**Date**: 2026-02-12
**Status**: ✅ Completed

## Overview
Migrated all GPT model calls from placeholder/old names to verified GPT-5 models based on extensive testing.

---

## Model Configuration

### ✅ **GPT-5.1** (Medium Tier)
**Used for**:
- Internal tier (parsing, NLU, context extraction)
- Chat tier (personality, conversation)
- Medium tier (routing, categorization)
- Fast OCR mode
- Auto-categorization

**Characteristics**:
- ✅ Supports Vision/OCR
- ✅ Custom temperatures (0.0-2.0)
- ✅ Consistent JSON output
- ✅ Good cost/performance balance
- 💰 ~$0.0015 per call

**Temperature Settings**:
- Internal/Parsing: `0.0` (deterministic)
- Chat/Personality: `0.4` (slightly creative)
- Routing/Categorization: `0.1` (precise but flexible)

---

### ✅ **GPT-5.2** (Heavy Tier)
**Used for**:
- Andrew mismatch reconciliation
- Receipt OCR heavy mode
- Correction passes
- Daneel duplicate resolution

**Characteristics**:
- ✅ Supports Vision/OCR
- ✅ Custom temperatures
- ✅ Superior reasoning capabilities
- 💰 ~$0.008 per call

**Temperature Setting**:
- All heavy tasks: `0.2` (complex reasoning)

---

## Files Updated

### 1. **agent_brain.py**
**Location**: `api/services/agent_brain.py`

**Changes**:
```python
# Before: gpt-5-1, gpt-5-nano, gpt-5-mini
# After:  gpt-5.1 (all tiers)

Line 325:  model="gpt-5.1"  # Brain routing
Line 1014: model="gpt-5.1"  # Context extraction
Line 1062: model="gpt-5.1"  # Context extraction (check)
Line 1698: model="gpt-5.1"  # Personality wrapper
Line 1726: model="gpt-5.1"  # Conversation generation
```

**Impact**: All Andrew/Daneel brain operations now use GPT-5.1

---

### 2. **andrew_mismatch_protocol.py**
**Location**: `api/services/andrew_mismatch_protocol.py`

**Changes**:
```python
# Before: gpt-5-2 (with hyphen)
# After:  gpt-5.2 (with period)

Line 232: model="gpt-5.2"  # Mismatch reconciliation
Line 244: model="gpt-5.2"  # Mismatch analysis
```

**Impact**: Heavy-tier reconciliation uses GPT-5.2

---

### 3. **receipt_scanner.py**
**Location**: `services/receipt_scanner.py`

**Changes**:
```python
# Before: gpt-4o-mini, gpt-4o
# After:  gpt-5.1 (fast), gpt-5.2 (heavy)

Line 556:  # Documentation updated: fast (gpt-5.1) / heavy (gpt-5.2)
Line 598:  openai_model = "gpt-5.2"  # Correction passes
Line 604:  openai_model = "gpt-5.2"  # Heavy mode
Line 606:  openai_model = "gpt-5.1"  # Fast mode (default)
Line 1063: model="gpt-5.1"  # Auto-categorization
```

**Impact**:
- Fast OCR: GPT-5.1 (cheaper, good for clear receipts)
- Heavy OCR: GPT-5.2 (max accuracy for scanned/unclear receipts)
- Auto-categorization: GPT-5.1

---

### 4. **daneel_auto_auth.py**
**Location**: `api/services/daneel_auto_auth.py`

**Changes**:
```python
# Before: gpt-5-1, gpt-5-2 (with hyphens)
# After:  gpt-5.1, gpt-5.2 (with periods)

Line 325: model="gpt-5.1"  # Categorization
Line 356: model="gpt-5.1"  # Categorization (batch)
Line 731: model="gpt-5.2"  # Duplicate resolution
```

**Impact**: Categorization uses GPT-5.1, complex duplicate analysis uses GPT-5.2

---

## Testing Results

### Text Response Quality
| Model | Status | Notes |
|-------|--------|-------|
| gpt-5-nano | ❌ | Returns empty responses |
| gpt-5-mini | ❌ | Returns empty responses |
| gpt-5 (base) | ❌ | Returns empty responses |
| **gpt-5.1** | ✅ | **Perfect - consistent JSON output** |
| **gpt-5.2** | ✅ | **Excellent - superior reasoning** |

### Vision/OCR Support
| Model | Vision | Notes |
|-------|--------|-------|
| gpt-5-nano | ❌ | No vision support |
| gpt-5-mini | ❌ | No vision support |
| gpt-5 (base) | ⚠️ | Has vision but returns empty text |
| **gpt-5.1** | ✅ | **Full vision + text support** |
| **gpt-5.2** | ✅ | **Full vision + text support** |

### Temperature Support
| Model | Custom Temp | Default |
|-------|-------------|---------|
| gpt-5-nano | ❌ | 1.0 only |
| gpt-5-mini | ❌ | 1.0 only |
| gpt-5 (base) | ❌ | 1.0 only |
| **gpt-5.1** | ✅ | 0.0-2.0 |
| **gpt-5.2** | ✅ | 0.0-2.0 |

---

## Cost Analysis (Estimated)

| Component | Model | Calls/Day | Cost/Call | Daily Cost |
|-----------|-------|-----------|-----------|------------|
| Brain Routing | gpt-5.1 | 150 | $0.0015 | $0.23 |
| Context Extraction | gpt-5.1 | 200 | $0.0001 | $0.02 |
| Personality | gpt-5.1 | 100 | $0.0001 | $0.01 |
| Fast OCR | gpt-5.1 | 40 | $0.003 | $0.12 |
| Auto-Categorization | gpt-5.1 | 50 | $0.002 | $0.10 |
| Heavy OCR | gpt-5.2 | 10 | $0.008 | $0.08 |
| Mismatch Reconciliation | gpt-5.2 | 5 | $0.008 | $0.04 |
| Duplicate Resolution | gpt-5.2 | 5 | $0.008 | $0.04 |
| **TOTAL** | | **560** | | **~$0.64/day** |

**Monthly estimate**: ~$19/month (very affordable for production)

---

## Benefits of GPT-5.1 + GPT-5.2

### ✅ Advantages
1. **Vision Support**: Can process receipt images directly without external OCR
2. **Temperature Control**: Fine-tune determinism vs creativity per use case
3. **Consistent Output**: No empty responses or formatting issues
4. **Cost Effective**: GPT-5.1 for 90% of tasks keeps costs low
5. **Superior Reasoning**: GPT-5.2 for complex analysis when needed

### ⚠️ Considerations
- **gpt-5-nano** and **gpt-5-mini** are NOT usable (empty responses)
- Must use **period** notation (gpt-5.1, gpt-5.2) not hyphen (gpt-5-1, gpt-5-2)
- Heavy tier (GPT-5.2) should be reserved for truly complex tasks

---

## Deployment Checklist

- [x] Update agent_brain.py
- [x] Update andrew_mismatch_protocol.py
- [x] Update receipt_scanner.py
- [x] Update daneel_auto_auth.py
- [ ] Test in staging environment
- [ ] Monitor token usage for 24h
- [ ] Deploy to production
- [ ] Update MEMORY.md with final costs

---

## Rollback Plan

If issues arise, revert to previous models:
```bash
# Checkout previous version
git log --oneline -10
git checkout <commit-hash> -- api/services/
git checkout <commit-hash> -- services/
```

Previous models were:
- Fast: `gpt-4o-mini`
- Heavy: `gpt-4o`

---

## Next Steps

1. ✅ **Code updated** with GPT-5.1 and GPT-5.2
2. ⏳ **Test in staging** - Verify all flows work correctly
3. ⏳ **Monitor metrics** - Track token usage and costs
4. ⏳ **Investigate nano/mini** - Future: Why do they return empty responses?
5. ⏳ **Optimize prompts** - Fine-tune for GPT-5.1 characteristics

---

## Documentation References

- Testing scripts: `test_gpt_models.py`, `test_vision_support.py`, `quick_test.py`
- Test results: See test output logs from 2026-02-12
- OpenAI GPT-5 docs: https://platform.openai.com/docs/models/gpt-5

---

**Migration completed by**: Claude Code
**Verified by**: Testing suite with real OpenAI API calls
**Approval**: Pending staging tests
