# utils/hashing.py
import hashlib


def generate_description_hash(description: str) -> str:
    """Generate MD5 hash of normalized description for cache lookups."""
    normalized = description.lower().strip()
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()
