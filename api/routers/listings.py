# api/routers/listings.py
# ================================
# Property Listings API Router (RentCast proxy)
# ================================
# Server-side proxy to the RentCast API (https://api.rentcast.io) for for-sale
# listings, so the browser never sees the API key and we can normalize + cache.
# This is the data feed for the Fix & Flip "Deal Finder" (bulk analysis): given
# a market (state/city/zip), return active sale listings normalized to a compact
# shape the calculator can run on.
#
# RentCast is used because it exposes active sale listings + AVM nationwide via a
# simple REST API without requiring MLS/broker authorization (unlike Zillow's
# Bridge API or MLSGrid). Set RENTCAST_API_KEY in the environment to go live; with
# no key the endpoint returns a small set of demo San Diego listings (source:
# "demo") so the feature is demonstrable end-to-end before a key is provisioned.

import asyncio
import os
import logging
from typing import Any, Dict, List, Optional, Union

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/listings", tags=["Listings"])

RENTCAST_BASE = "https://api.rentcast.io/v1"
RENTCAST_TIMEOUT = 25.0


class ListingSearch(BaseModel):
    state: Optional[str] = None                              # 2-letter, e.g. "CA"
    city: Optional[str] = None
    zip_code: Optional[str] = None
    # Single string (legacy) OR list (multi-type filter). Empty string / empty
    # list both mean "no filter". RentCast's API only accepts a single type per
    # call, so when a list is provided we fan out one call per type and merge.
    property_type: Optional[Union[str, List[str]]] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[float] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    days_old: Optional[int] = None                           # only listings active within N days
    limit: int = 50

    def normalized_types(self) -> List[str]:
        """Return the requested property types as a clean, dedup'd list. Empty
        list means 'no type filter'. Trims whitespace and drops empty entries
        so the legacy '' sentinel ('Any') from older clients still works."""
        if self.property_type is None:
            return []
        if isinstance(self.property_type, str):
            t = self.property_type.strip()
            return [t] if t else []
        seen: List[str] = []
        for t in self.property_type:
            if not isinstance(t, str):
                continue
            tn = t.strip()
            if tn and tn not in seen:
                seen.append(tn)
        return seen


def _normalize(item: Dict[str, Any]) -> Dict[str, Any]:
    """Map a RentCast sale-listing object to the compact shape the UI expects."""
    return {
        "id": item.get("id") or item.get("mlsNumber") or item.get("formattedAddress"),
        "address": item.get("formattedAddress") or item.get("addressLine1"),
        "city": item.get("city"),
        "state": item.get("state"),
        "zip": item.get("zipCode"),
        "county": item.get("county"),
        "lat": item.get("latitude"),
        "lng": item.get("longitude"),
        "property_type": item.get("propertyType"),
        "bedrooms": item.get("bedrooms"),
        "bathrooms": item.get("bathrooms"),
        "sqft": item.get("squareFootage"),
        "lot_size": item.get("lotSize"),
        "year_built": item.get("yearBuilt"),
        "price": item.get("price"),
        "status": item.get("status"),
        "days_on_market": item.get("daysOnMarket"),
        "listed_date": item.get("listedDate"),
        "mls_name": item.get("mlsName"),
    }


# Demo data so the Deal Finder works without a RENTCAST_API_KEY. Realistic San
# Diego for-sale rows; flagged source: "demo" so the UI can label them clearly.
_DEMO_LISTINGS: List[Dict[str, Any]] = [
    {"id": "demo-1", "address": "3812 Marlborough Ave, San Diego, CA 92105", "city": "San Diego", "state": "CA",
     "zip": "92105", "county": "San Diego", "lat": 32.748, "lng": -117.097, "property_type": "Single Family",
     "bedrooms": 3, "bathrooms": 2, "sqft": 1320, "lot_size": 6000, "year_built": 1952, "price": 689000,
     "status": "Active", "days_on_market": 12, "listed_date": "2026-05-15", "mls_name": "CRMLS"},
    {"id": "demo-2", "address": "4521 33rd St, San Diego, CA 92116", "city": "San Diego", "state": "CA",
     "zip": "92116", "county": "San Diego", "lat": 32.762, "lng": -117.128, "property_type": "Single Family",
     "bedrooms": 2, "bathrooms": 1, "sqft": 980, "lot_size": 4500, "year_built": 1941, "price": 735000,
     "status": "Active", "days_on_market": 5, "listed_date": "2026-05-22", "mls_name": "CRMLS"},
    {"id": "demo-3", "address": "1290 Hornblend St, San Diego, CA 92109", "city": "San Diego", "state": "CA",
     "zip": "92109", "county": "San Diego", "lat": 32.799, "lng": -117.252, "property_type": "Condo",
     "bedrooms": 2, "bathrooms": 2, "sqft": 1100, "lot_size": 0, "year_built": 1978, "price": 815000,
     "status": "Active", "days_on_market": 28, "listed_date": "2026-04-29", "mls_name": "CRMLS"},
    {"id": "demo-4", "address": "5440 Reservoir Dr, San Diego, CA 92115", "city": "San Diego", "state": "CA",
     "zip": "92115", "county": "San Diego", "lat": 32.760, "lng": -117.073, "property_type": "Single Family",
     "bedrooms": 4, "bathrooms": 2, "sqft": 1680, "lot_size": 7200, "year_built": 1959, "price": 905000,
     "status": "Active", "days_on_market": 41, "listed_date": "2026-04-16", "mls_name": "CRMLS"},
    {"id": "demo-5", "address": "2738 B St, San Diego, CA 92102", "city": "San Diego", "state": "CA",
     "zip": "92102", "county": "San Diego", "lat": 32.717, "lng": -117.134, "property_type": "Single Family",
     "bedrooms": 3, "bathrooms": 1, "sqft": 1150, "lot_size": 5200, "year_built": 1925, "price": 649000,
     "status": "Active", "days_on_market": 8, "listed_date": "2026-05-19", "mls_name": "CRMLS"},
    {"id": "demo-6", "address": "4910 Mansfield St, San Diego, CA 92116", "city": "San Diego", "state": "CA",
     "zip": "92116", "county": "San Diego", "lat": 32.768, "lng": -117.115, "property_type": "Single Family",
     "bedrooms": 3, "bathrooms": 2, "sqft": 1440, "lot_size": 5800, "year_built": 1948, "price": 799000,
     "status": "Active", "days_on_market": 17, "listed_date": "2026-05-10", "mls_name": "CRMLS"},
]


def _filter(rows: List[Dict[str, Any]], payload: ListingSearch) -> List[Dict[str, Any]]:
    out = rows
    if payload.min_price is not None:
        out = [r for r in out if (r.get("price") or 0) >= payload.min_price]
    if payload.max_price is not None:
        out = [r for r in out if (r.get("price") or 0) <= payload.max_price]
    types = payload.normalized_types()
    if types:
        wanted = {t.lower() for t in types}
        out = [r for r in out if (r.get("property_type") or "").lower() in wanted]
    if payload.bedrooms is not None:
        out = [r for r in out if (r.get("bedrooms") or 0) >= payload.bedrooms]
    if payload.zip_code:
        out = [r for r in out if str(r.get("zip") or "") == str(payload.zip_code)]
    return out


def _build_rentcast_params(payload: ListingSearch, limit: int, property_type: Optional[str]) -> Dict[str, Any]:
    """Translate one search payload (+ a single property type, if any) into the
    query params RentCast expects. Factored out so the multi-type fan-out can
    reuse the same translation per type."""
    params: Dict[str, Any] = {"status": "Active", "limit": limit}
    if payload.city:
        params["city"] = payload.city
    if payload.state:
        params["state"] = payload.state.upper()
    if payload.zip_code:
        params["zipCode"] = payload.zip_code
    if property_type:
        params["propertyType"] = property_type
    if payload.bedrooms is not None:
        params["bedrooms"] = payload.bedrooms
    if payload.bathrooms is not None:
        params["bathrooms"] = payload.bathrooms
    if payload.days_old is not None:
        params["daysOld"] = payload.days_old
    return params


async def _rentcast_fetch(
    client: httpx.AsyncClient, api_key: str, params: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Hit RentCast once and return normalized rows. Raises HTTPException on
    non-recoverable provider errors so the caller can short-circuit."""
    r = await client.get(
        f"{RENTCAST_BASE}/listings/sale",
        params=params,
        headers={"X-Api-Key": api_key, "Accept": "application/json"},
        timeout=RENTCAST_TIMEOUT,
    )
    if r.status_code == 401:
        raise HTTPException(status_code=502, detail="Listings provider rejected the API key (401).")
    if r.status_code == 429:
        raise HTTPException(status_code=429, detail="Listings provider quota exceeded. Try again later.")
    if r.status_code >= 400:
        logger.warning("[LISTINGS] RentCast %s: %s", r.status_code, r.text[:300])
        raise HTTPException(status_code=502, detail=f"Listings provider returned {r.status_code}.")
    data = r.json()
    items = data if isinstance(data, list) else (data.get("listings") or data.get("data") or [])
    return [_normalize(it) for it in items]


@router.post("/search")
async def search_listings(payload: ListingSearch, current_user: dict = Depends(get_current_user)):
    """Search active for-sale listings for a market. Proxies RentCast; falls back
    to demo data when RENTCAST_API_KEY is not configured.

    property_type may be a string or a list of strings. RentCast accepts only one
    propertyType per call, so when the caller asks for several we fan out one
    request per type in parallel and merge by id. Single-type and unfiltered
    runs stay at exactly one quota request."""
    api_key = os.getenv("RENTCAST_API_KEY")
    # RentCast returns up to 500 records per call and bills the same 1 request
    # regardless of how many come back, so we let a single search pull the full 500.
    limit = max(1, min(payload.limit or 50, 500))

    if not api_key:
        rows = _filter(_DEMO_LISTINGS, payload)[:limit]
        return {"listings": rows, "count": len(rows), "source": "demo",
                "note": "Demo data — set RENTCAST_API_KEY on the server to pull live listings."}

    if not (payload.state or payload.city or payload.zip_code):
        raise HTTPException(status_code=400, detail="Provide at least a state, city, or ZIP code.")

    types = payload.normalized_types()

    try:
        async with httpx.AsyncClient() as client:
            if len(types) > 1:
                # Multi-type fan-out: 1 RentCast call per type, merged + dedup'd
                # by listing id. Note: this multiplies the quota cost by the
                # number of types — keep the picker bounded on the frontend.
                tasks = [
                    _rentcast_fetch(client, api_key, _build_rentcast_params(payload, limit, t))
                    for t in types
                ]
                results = await asyncio.gather(*tasks)
                seen: set = set()
                rows: List[Dict[str, Any]] = []
                for batch in results:
                    for row in batch:
                        rid = row.get("id")
                        # No id (rare): fall back to address to dedupe; else keep.
                        key = rid if rid is not None else row.get("address")
                        if key is not None and key in seen:
                            continue
                        if key is not None:
                            seen.add(key)
                        rows.append(row)
            else:
                # Single-type or unfiltered: one RentCast call (unchanged path).
                single = types[0] if types else None
                rows = await _rentcast_fetch(
                    client, api_key, _build_rentcast_params(payload, limit, single)
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[LISTINGS] RentCast request failed: %r", e)
        raise HTTPException(status_code=502, detail=f"Listings provider error: {e}")

    # Price + bedrooms + zip filters that RentCast doesn't support as query params.
    # Also re-applies the type filter as a safety net in case RentCast returns a
    # row that doesn't match what we asked for (which we've seen happen).
    rows = _filter(rows, payload)
    return {"listings": rows, "count": len(rows), "source": "rentcast"}
