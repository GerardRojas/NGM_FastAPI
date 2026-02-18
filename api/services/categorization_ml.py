# api/services/categorization_ml.py
# ============================================================================
# ML-based Categorization Service - TF-IDF + k-NN expense classification
# ============================================================================
# Trains on historical expense data (expenses_manual_COGS) with confidence
# weighting from categorization_cache and categorization_corrections.
#
# Usage:
#   from api.services.categorization_ml import get_ml_service
#
#   ml = get_ml_service()
#   ml.ensure_trained(supabase)
#   result = ml.predict("Drywall 4x8 sheet")
#   results = ml.predict_batch([{"description": "Lumber 2x4"}, ...])
# ============================================================================

import gc
import hashlib
import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.neighbors import NearestNeighbors

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────
MIN_TRAINING_ROWS = 50
STALE_AFTER_SECONDS = 6 * 60 * 60  # 6 hours
DEFAULT_MIN_CONFIDENCE = 90.0
DEFAULT_N_NEIGHBORS = 5
MAX_FEATURES = 5000
BATCH_FETCH_SIZE = 1000  # rows per Supabase fetch page
MAX_TRAINING_ROWS = 2000  # cap to limit memory usage during training


# ── Text Preprocessing ──────────────────────────────────────────

def _preprocess_text(text: str) -> str:
    """Normalize a description string for TF-IDF vectorization.

    Steps:
      1. Lowercase + strip
      2. Remove special chars except alphanumerics, hyphens, spaces
      3. Normalize number+unit patterns ("8ft" -> "8 ft") but keep
         dimension-like tokens intact ("2x4" stays)
      4. Collapse multiple spaces
    """
    if not text or not isinstance(text, str):
        return ""
    t = text.lower().strip()
    # Remove special chars except letters, digits, hyphens, spaces
    t = re.sub(r"[^a-z0-9\s\-]", " ", t)
    # Normalize number+unit: "8ft" -> "8 ft", "12in" -> "12 in"
    # But NOT "2x4" (dimension pattern) - keep as-is
    t = re.sub(r"(\d)([a-z])", r"\1 \2", t)
    # Collapse whitespace
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _generate_description_hash(description: str) -> str:
    """Generate MD5 hash of normalized description for cache lookups.

    Matches the hashing in receipt_scanner.py so cache joins work.
    """
    normalized = description.lower().strip()
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


# ── Core Service ─────────────────────────────────────────────────

class CategorizationMLService:
    """TF-IDF + k-NN categorization trained on historical expense data."""

    def __init__(self):
        self.vectorizer: Optional[TfidfVectorizer] = None
        self.nn_model: Optional[NearestNeighbors] = None
        self.training_data: Optional[pd.DataFrame] = None
        self.is_trained: bool = False
        self.last_trained_at: Optional[datetime] = None
        self.training_size: int = 0
        self.feature_count: int = 0
        self._lock = threading.Lock()

    # ── Training ─────────────────────────────────────────────────

    def train(self, supabase) -> dict:
        """Load data from DB and train the TF-IDF + k-NN model.

        Data sources (joined by description):
          - expenses_manual_COGS: descriptions + account_id
          - accounts: account_name via account_id join
          - categorization_cache: confidence for cached items (MD5 hash match)
          - categorization_corrections: overrides with confidence=100

        Returns:
            dict with training metrics (rows, features, time_ms, status).
        """
        with self._lock:
            t0 = time.monotonic()
            logger.info("[ML-CAT] Training started...")

            try:
                # ── Step 1: Fetch expense data with account names ────
                df = self._fetch_training_data(supabase)

                if df is None or len(df) < MIN_TRAINING_ROWS:
                    row_count = 0 if df is None else len(df)
                    self.is_trained = False
                    msg = (
                        f"Insufficient training data: {row_count} rows "
                        f"(minimum {MIN_TRAINING_ROWS})"
                    )
                    logger.warning("[ML-CAT] %s", msg)
                    return {
                        "status": "insufficient_data",
                        "rows": row_count,
                        "min_required": MIN_TRAINING_ROWS,
                        "message": msg,
                    }

                # ── Step 2: Fetch cache confidences ──────────────────
                df = self._enrich_with_cache_confidence(supabase, df)

                # ── Step 3: Apply corrections (override account) ─────
                df = self._apply_corrections(supabase, df)

                # ── Step 4: Preprocess descriptions ──────────────────
                df["processed"] = df["description"].apply(_preprocess_text)
                # Drop rows with empty processed text
                df = df[df["processed"].str.len() > 0].reset_index(drop=True)

                if len(df) < MIN_TRAINING_ROWS:
                    self.is_trained = False
                    msg = (
                        f"After preprocessing, only {len(df)} rows remain "
                        f"(minimum {MIN_TRAINING_ROWS})"
                    )
                    logger.warning("[ML-CAT] %s", msg)
                    return {
                        "status": "insufficient_data",
                        "rows": len(df),
                        "min_required": MIN_TRAINING_ROWS,
                        "message": msg,
                    }

                # ── Step 5: Free old models before allocating new ones ─
                if self.vectorizer is not None:
                    del self.vectorizer
                if self.nn_model is not None:
                    del self.nn_model
                if self.training_data is not None:
                    del self.training_data
                gc.collect()

                # ── Step 6: Build TF-IDF matrix ──────────────────────
                self.vectorizer = TfidfVectorizer(
                    max_features=MAX_FEATURES,
                    ngram_range=(1, 2),
                    sublinear_tf=True,
                    min_df=2,
                )
                tfidf_matrix = self.vectorizer.fit_transform(df["processed"])

                # ── Step 7: Fit k-NN model ───────────────────────────
                self.nn_model = NearestNeighbors(
                    n_neighbors=min(DEFAULT_N_NEIGHBORS, len(df)),
                    metric="cosine",
                    algorithm="brute",
                )
                self.nn_model.fit(tfidf_matrix)

                # ── Step 8: Store training metadata ──────────────────
                self.training_data = df[
                    ["description", "account_id", "account_name", "confidence"]
                ].reset_index(drop=True)
                self.is_trained = True
                self.last_trained_at = datetime.now(timezone.utc)
                self.training_size = len(df)
                self.feature_count = tfidf_matrix.shape[1]

                elapsed_ms = int((time.monotonic() - t0) * 1000)
                unique_accounts = df["account_id"].nunique()

                # Free temporary training artifacts to reduce memory pressure
                del df, tfidf_matrix
                gc.collect()

                logger.info(
                    "[ML-CAT] Training complete: %d rows, %d features, "
                    "%d accounts, %dms",
                    self.training_size,
                    self.feature_count,
                    unique_accounts,
                    elapsed_ms,
                )

                return {
                    "status": "trained",
                    "rows": self.training_size,
                    "features": self.feature_count,
                    "unique_accounts": unique_accounts,
                    "time_ms": elapsed_ms,
                    "trained_at": self.last_trained_at.isoformat(),
                }

            except Exception as e:
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                self.is_trained = False
                logger.error("[ML-CAT] Training failed (%dms): %s", elapsed_ms, e)
                return {
                    "status": "error",
                    "message": str(e),
                    "time_ms": elapsed_ms,
                }

    def _fetch_training_data(self, supabase) -> Optional[pd.DataFrame]:
        """Fetch expenses with account names via paginated Supabase queries.

        Returns DataFrame with columns: description, account_id, account_name.
        """
        # Fetch all accounts into a lookup dict
        accounts_resp = supabase.table("accounts") \
            .select("account_id, Name") \
            .execute()

        if not accounts_resp.data:
            logger.warning("[ML-CAT] No accounts found in DB")
            return None

        account_map = {
            row["account_id"]: row["Name"]
            for row in accounts_resp.data
            if row.get("account_id") and row.get("Name")
        }

        # Paginated fetch of expenses with descriptions and account_ids
        all_rows = []
        offset = 0

        while True:
            resp = supabase.table("expenses_manual_COGS") \
                .select("LineDescription, account_id") \
                .not_.is_("LineDescription", "null") \
                .not_.is_("account_id", "null") \
                .range(offset, offset + BATCH_FETCH_SIZE - 1) \
                .execute()

            if not resp.data:
                break

            all_rows.extend(resp.data)

            # Stop early if we've reached the training-row cap
            if len(all_rows) >= MAX_TRAINING_ROWS:
                all_rows = all_rows[:MAX_TRAINING_ROWS]
                logger.info(
                    "[ML-CAT] Reached MAX_TRAINING_ROWS (%d), stopping fetch",
                    MAX_TRAINING_ROWS,
                )
                break

            if len(resp.data) < BATCH_FETCH_SIZE:
                break
            offset += BATCH_FETCH_SIZE

        if not all_rows:
            logger.warning("[ML-CAT] No expense rows with descriptions found")
            return None

        logger.info("[ML-CAT] Fetched %d expense rows", len(all_rows))

        # Build DataFrame and join account names
        df = pd.DataFrame(all_rows)
        df.rename(columns={"LineDescription": "description"}, inplace=True)

        # Filter out empty descriptions
        df = df[df["description"].str.strip().str.len() > 0].copy()

        # Map account names
        df["account_name"] = df["account_id"].map(account_map)

        # Drop rows where account_id has no matching name (orphaned references)
        df = df.dropna(subset=["account_name"]).reset_index(drop=True)

        # Default confidence = 100 (human-entered, no cache entry means manual)
        df["confidence"] = 100.0

        return df

    def _enrich_with_cache_confidence(
        self, supabase, df: pd.DataFrame
    ) -> pd.DataFrame:
        """Join with categorization_cache to pull stored confidence values.

        Items that were auto-categorized will have a cache entry with their
        original confidence score. Items with no cache entry are assumed to be
        human-entered (confidence = 100, already the default).
        """
        try:
            # Compute hashes for all descriptions
            df["desc_hash"] = df["description"].apply(_generate_description_hash)
            unique_hashes = df["desc_hash"].unique().tolist()

            if not unique_hashes:
                return df

            # Fetch cache entries in batches (Supabase has URL length limits)
            cache_map = {}
            hash_batch_size = 200
            for i in range(0, len(unique_hashes), hash_batch_size):
                batch = unique_hashes[i : i + hash_batch_size]
                resp = supabase.table("categorization_cache") \
                    .select("description_hash, confidence") \
                    .in_("description_hash", batch) \
                    .execute()

                if resp.data:
                    for row in resp.data:
                        h = row["description_hash"]
                        conf = row.get("confidence", 100)
                        # Keep the most recent (highest) confidence per hash
                        if h not in cache_map or conf > cache_map[h]:
                            cache_map[h] = conf

            # Apply cached confidence (overwrite default 100 only for cached items)
            if cache_map:
                df["cache_confidence"] = df["desc_hash"].map(cache_map)
                mask = df["cache_confidence"].notna()
                df.loc[mask, "confidence"] = df.loc[mask, "cache_confidence"]
                df.drop(columns=["cache_confidence"], inplace=True)
                logger.info(
                    "[ML-CAT] Enriched %d rows with cache confidence",
                    mask.sum(),
                )

            df.drop(columns=["desc_hash"], inplace=True)

        except Exception as e:
            logger.warning("[ML-CAT] Cache enrichment failed (non-fatal): %s", e)

        return df

    def _apply_corrections(
        self, supabase, df: pd.DataFrame
    ) -> pd.DataFrame:
        """Apply user corrections: override account_id/name for corrected items.

        Corrections are considered ground truth (confidence = 100).
        For each unique description that has a correction, update the
        account mapping in the training data to use the corrected account.
        """
        try:
            resp = supabase.table("categorization_corrections") \
                .select(
                    "description, corrected_account_id, corrected_account_name"
                ) \
                .order("created_at", desc=True) \
                .execute()

            if not resp.data:
                return df

            # Build correction map: description (lowered) -> latest correction
            correction_map = {}
            for row in resp.data:
                desc = row.get("description", "").lower().strip()
                if desc and desc not in correction_map:
                    # First seen = most recent (ordered DESC)
                    correction_map[desc] = {
                        "account_id": row["corrected_account_id"],
                        "account_name": row["corrected_account_name"],
                    }

            if not correction_map:
                return df

            # Apply corrections to matching rows
            applied = 0
            df["desc_lower"] = df["description"].str.lower().str.strip()
            for desc_key, correction in correction_map.items():
                mask = df["desc_lower"] == desc_key
                if mask.any():
                    df.loc[mask, "account_id"] = correction["account_id"]
                    df.loc[mask, "account_name"] = correction["account_name"]
                    df.loc[mask, "confidence"] = 100.0
                    applied += mask.sum()

            df.drop(columns=["desc_lower"], inplace=True)
            logger.info(
                "[ML-CAT] Applied %d corrections (%d unique patterns)",
                applied,
                len(correction_map),
            )

        except Exception as e:
            logger.warning("[ML-CAT] Corrections apply failed (non-fatal): %s", e)

        return df

    # ── Prediction ───────────────────────────────────────────────

    def predict(
        self,
        description: str,
        construction_stage: str = None,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    ) -> Optional[dict]:
        """Predict the expense category for a single description.

        Uses k-NN with confidence-weighted voting:
          - Each neighbor contributes: weight = (1 - distance) * (confidence / 100)
          - Votes are grouped by account_id and summed
          - Winner = highest total weight
          - Final confidence = winner_weight / total_weights * 100

        Args:
            description: The expense line description.
            construction_stage: Optional stage (reserved for future stage-aware models).
            min_confidence: Minimum confidence to return a prediction.

        Returns:
            dict with {account_id, account_name, confidence, source, neighbors}
            or None if below threshold or model not trained.
        """
        if not self.is_trained or self.vectorizer is None or self.nn_model is None:
            return None

        processed = _preprocess_text(description)
        if not processed:
            return None

        try:
            # Vectorize the input
            query_vec = self.vectorizer.transform([processed])

            # Find nearest neighbors
            k = self.nn_model.n_neighbors
            distances, indices = self.nn_model.kneighbors(
                query_vec, n_neighbors=k
            )

            distances = distances[0]  # flatten from 2D
            indices = indices[0]

            # Build neighbor list with weights
            neighbors = []
            for dist, idx in zip(distances, indices):
                row = self.training_data.iloc[idx]
                training_conf = float(row["confidence"])
                similarity = 1.0 - float(dist)  # cosine distance -> similarity
                weight = max(similarity, 0.0) * (training_conf / 100.0)
                neighbors.append({
                    "account_id": row["account_id"],
                    "account_name": row["account_name"],
                    "description": row["description"],
                    "distance": round(float(dist), 4),
                    "similarity": round(similarity, 4),
                    "training_confidence": training_conf,
                    "weight": round(weight, 4),
                })

            # Confidence-weighted voting by account_id
            votes: dict[str, dict] = {}
            total_weight = 0.0

            for n in neighbors:
                aid = n["account_id"]
                w = n["weight"]
                total_weight += w
                if aid not in votes:
                    votes[aid] = {
                        "account_id": aid,
                        "account_name": n["account_name"],
                        "total_weight": 0.0,
                    }
                votes[aid]["total_weight"] += w

            if total_weight <= 0:
                return None

            # Pick winner
            winner = max(votes.values(), key=lambda v: v["total_weight"])
            final_confidence = (winner["total_weight"] / total_weight) * 100.0

            if final_confidence < min_confidence:
                logger.debug(
                    "[ML-CAT] Prediction below threshold: %.1f < %.1f for '%s'",
                    final_confidence,
                    min_confidence,
                    description[:60],
                )
                return None

            return {
                "account_id": winner["account_id"],
                "account_name": winner["account_name"],
                "confidence": round(final_confidence, 1),
                "source": "ml",
                "neighbors": neighbors,
            }

        except Exception as e:
            logger.error("[ML-CAT] Prediction error for '%s': %s", description[:60], e)
            return None

    def predict_batch(
        self,
        items: list[dict],
        construction_stage: str = None,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    ) -> list[Optional[dict]]:
        """Batch prediction for multiple items.

        Each item should have a "description" key.
        Returns a list of results (same order), None for items below threshold.

        For efficiency, vectorizes all descriptions at once and runs a single
        k-NN query instead of N individual ones.
        """
        if not self.is_trained or self.vectorizer is None or self.nn_model is None:
            return [None] * len(items)

        if not items:
            return []

        try:
            # Preprocess all descriptions
            descriptions = [
                item.get("description", "") if isinstance(item, dict) else str(item)
                for item in items
            ]
            processed = [_preprocess_text(d) for d in descriptions]

            # Find items with valid text
            valid_indices = [i for i, p in enumerate(processed) if p]
            if not valid_indices:
                return [None] * len(items)

            valid_texts = [processed[i] for i in valid_indices]

            # Vectorize all at once
            query_matrix = self.vectorizer.transform(valid_texts)

            # k-NN for all queries at once
            k = self.nn_model.n_neighbors
            distances_all, indices_all = self.nn_model.kneighbors(
                query_matrix, n_neighbors=k
            )

            # Process each result
            results: list[Optional[dict]] = [None] * len(items)

            for batch_idx, orig_idx in enumerate(valid_indices):
                distances = distances_all[batch_idx]
                indices = indices_all[batch_idx]

                neighbors = []
                for dist, idx in zip(distances, indices):
                    row = self.training_data.iloc[idx]
                    training_conf = float(row["confidence"])
                    similarity = 1.0 - float(dist)
                    weight = max(similarity, 0.0) * (training_conf / 100.0)
                    neighbors.append({
                        "account_id": row["account_id"],
                        "account_name": row["account_name"],
                        "description": row["description"],
                        "distance": round(float(dist), 4),
                        "similarity": round(similarity, 4),
                        "training_confidence": training_conf,
                        "weight": round(weight, 4),
                    })

                # Weighted voting
                votes: dict[str, dict] = {}
                total_weight = 0.0
                for n in neighbors:
                    aid = n["account_id"]
                    w = n["weight"]
                    total_weight += w
                    if aid not in votes:
                        votes[aid] = {
                            "account_id": aid,
                            "account_name": n["account_name"],
                            "total_weight": 0.0,
                        }
                    votes[aid]["total_weight"] += w

                if total_weight <= 0:
                    continue

                winner = max(votes.values(), key=lambda v: v["total_weight"])
                final_confidence = (winner["total_weight"] / total_weight) * 100.0

                if final_confidence >= min_confidence:
                    results[orig_idx] = {
                        "account_id": winner["account_id"],
                        "account_name": winner["account_name"],
                        "confidence": round(final_confidence, 1),
                        "source": "ml",
                        "neighbors": neighbors,
                    }

            classified = sum(1 for r in results if r is not None)
            logger.info(
                "[ML-CAT] Batch prediction: %d/%d items classified (threshold %.0f)",
                classified,
                len(items),
                min_confidence,
            )
            return results

        except Exception as e:
            logger.error("[ML-CAT] Batch prediction error: %s", e)
            return [None] * len(items)

    # ── Status & Lifecycle ───────────────────────────────────────

    def get_status(self) -> dict:
        """Return current model status for health checks and diagnostics."""
        return {
            "is_trained": self.is_trained,
            "training_size": self.training_size,
            "feature_count": self.feature_count,
            "last_trained_at": (
                self.last_trained_at.isoformat() if self.last_trained_at else None
            ),
            "stale": self._is_stale(),
            "n_neighbors": (
                self.nn_model.n_neighbors if self.nn_model is not None else None
            ),
        }

    def _is_stale(self) -> bool:
        """Check if the model is stale and should be retrained."""
        if not self.is_trained or self.last_trained_at is None:
            return True
        age = (datetime.now(timezone.utc) - self.last_trained_at).total_seconds()
        return age > STALE_AFTER_SECONDS

    def ensure_trained(self, supabase) -> None:
        """Train the model if it has not been trained yet or is stale (>6 hours).

        This is the recommended entry point before calling predict/predict_batch.
        Safe to call frequently - uses a lock to prevent concurrent retraining.
        """
        if self.is_trained and not self._is_stale():
            return

        logger.info(
            "[ML-CAT] Model %s, triggering training...",
            "stale" if self.is_trained else "not trained",
        )
        result = self.train(supabase)
        status = result.get("status", "unknown")
        if status == "trained":
            logger.info(
                "[ML-CAT] Model ready: %d rows, %d features",
                result.get("rows", 0),
                result.get("features", 0),
            )
        else:
            logger.warning("[ML-CAT] Training result: %s", status)


# ── Singleton ────────────────────────────────────────────────────

_ml_service = CategorizationMLService()


def get_ml_service() -> CategorizationMLService:
    """Get the singleton ML categorization service instance."""
    return _ml_service
