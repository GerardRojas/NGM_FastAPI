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
    # Radial search (map-driven). When all three are set the request takes
    # precedence over city/zip and goes to RentCast as a radius query. radius
    # is in MILES; RentCast caps it server-side (~50 mi typical).
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    radius: Optional[float] = None
    limit: int = 50

    def has_radial_search(self) -> bool:
        return (
            self.latitude is not None
            and self.longitude is not None
            and self.radius is not None
            and self.radius > 0
        )

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


def _contact(obj: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Compact a RentCast listingAgent / listingOffice object to {name, phone,
    email, website}, dropping empty fields. None when nothing is present."""
    if not isinstance(obj, dict):
        return None
    out = {k: obj.get(k) for k in ("name", "phone", "email", "website") if obj.get(k)}
    return out or None


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
        # B: listing agent + brokerage contact (name/phone/email/website).
        "listing_agent": _contact(item.get("listingAgent")),
        "listing_office": _contact(item.get("listingOffice")),
    }


# Demo data so the Deal Finder works without a RENTCAST_API_KEY. Realistic San
# Diego for-sale rows; flagged source: "demo" so the UI can label them clearly.
_DEMO_LISTINGS: List[Dict[str, Any]] = [
    {"id": "demo-1", "address": "3812 Marlborough Ave, San Diego, CA 92105", "city": "San Diego", "state": "CA",
     "zip": "92105", "county": "San Diego", "lat": 32.748, "lng": -117.097, "property_type": "Single Family",
     "bedrooms": 3, "bathrooms": 2, "sqft": 1320, "lot_size": 6000, "year_built": 1952, "price": 689000,
     "status": "Active", "days_on_market": 12, "listed_date": "2026-05-15", "mls_name": "CRMLS",
     "listing_agent": {"name": "Jordan Avery", "phone": "6195550142", "email": "javery@example.com"},
     "listing_office": {"name": "Pacific Crest Realty", "phone": "6195550100"}},
    {"id": "demo-2", "address": "4521 33rd St, San Diego, CA 92116", "city": "San Diego", "state": "CA",
     "zip": "92116", "county": "San Diego", "lat": 32.762, "lng": -117.128, "property_type": "Single Family",
     "bedrooms": 2, "bathrooms": 1, "sqft": 980, "lot_size": 4500, "year_built": 1941, "price": 735000,
     "status": "Active", "days_on_market": 5, "listed_date": "2026-05-22", "mls_name": "CRMLS",
     "listing_agent": {"name": "Sam Delgado", "phone": "6195550199", "email": "sdelgado@example.com"}},
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


def _haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two lat/lng points, in miles. Used for the
    demo data path so a radial search can pre-filter without RentCast."""
    import math
    r_miles = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r_miles * math.asin(math.sqrt(a))


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
    if payload.zip_code and not payload.has_radial_search():
        out = [r for r in out if str(r.get("zip") or "") == str(payload.zip_code)]
    if payload.has_radial_search():
        lat0, lng0, r = payload.latitude, payload.longitude, payload.radius
        out = [
            row for row in out
            if row.get("lat") is not None and row.get("lng") is not None
            and _haversine_miles(lat0, lng0, row["lat"], row["lng"]) <= r
        ]
    return out


def _build_rentcast_params(payload: ListingSearch, limit: int, property_type: Optional[str]) -> Dict[str, Any]:
    """Translate one search payload (+ a single property type, if any) into the
    query params RentCast expects. Factored out so the multi-type fan-out can
    reuse the same translation per type.

    When a radial search (lat/lng/radius) is requested the city/zip filters are
    omitted — RentCast treats lat/lng/radius as the search anchor. state is
    still passed if set so cross-state radii get bounded."""
    params: Dict[str, Any] = {"status": "Active", "limit": limit}
    radial = payload.has_radial_search()
    if radial:
        params["latitude"] = payload.latitude
        params["longitude"] = payload.longitude
        params["radius"] = payload.radius
    else:
        if payload.city:
            params["city"] = payload.city
        if payload.zip_code:
            params["zipCode"] = payload.zip_code
    if payload.state:
        params["state"] = payload.state.upper()
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

    if not (payload.state or payload.city or payload.zip_code or payload.has_radial_search()):
        raise HTTPException(
            status_code=400,
            detail="Provide a state, city, ZIP code, or a lat/lng/radius search area.",
        )

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


# ============================================================================
# Geocoder + Comparable sales (RentCast AVM)
# ============================================================================
# Geocoding sits next to listings so the Calculator can validate / locate an
# address before pulling comps. Census is free and county-wide; no quota cost.
# Comps use RentCast's AVM endpoint which returns a value estimate AND the 3-5
# comparable sale properties it used — same vendor, same key, same quota pool
# (no new contract). Demo mode mirrors the listings demo path so the feature
# works end-to-end without RENTCAST_API_KEY.

CENSUS_GEOCODER_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
# Nominatim's usage policy requires a descriptive User-Agent. Used only as a
# fallback (when Census returns nothing) + manual reverse lookups, so volume is low.
NOMINATIM_HEADERS = {"User-Agent": "NGM-Hub/1.0 (real-estate calculator; contact german@ngmanagements.com)"}
GEOCODE_MAX_MATCHES = 5


class GeocodeRequest(BaseModel):
    address: str


class ReverseGeocodeRequest(BaseModel):
    latitude: float
    longitude: float


async def _census_matches(client: httpx.AsyncClient, addr: str) -> List[Dict[str, Any]]:
    """Up to GEOCODE_MAX_MATCHES Census candidates. [] on any failure (caller
    falls back to Nominatim) — never raises, so one provider being down isn't fatal."""
    try:
        r = await client.get(
            CENSUS_GEOCODER_URL,
            params={"address": addr, "benchmark": "Public_AR_Current", "format": "json"},
            timeout=20.0,
        )
        r.raise_for_status()
        raw = ((r.json().get("result") or {}).get("addressMatches")) or []
    except Exception as e:
        logger.warning("[LISTINGS] Census geocoder failed (will try fallback): %r", e)
        return []
    out: List[Dict[str, Any]] = []
    for m in raw[:GEOCODE_MAX_MATCHES]:
        c = m.get("coordinates") or {}
        if "x" in c and "y" in c:
            out.append({"lat": c["y"], "lng": c["x"], "matched_address": m.get("matchedAddress") or addr, "source": "census"})
    return out


async def _nominatim_matches(client: httpx.AsyncClient, addr: str) -> List[Dict[str, Any]]:
    """OpenStreetMap/Nominatim fallback for addresses Census can't resolve."""
    try:
        r = await client.get(
            NOMINATIM_SEARCH_URL,
            params={"q": addr, "format": "json", "limit": GEOCODE_MAX_MATCHES, "countrycodes": "us", "addressdetails": 0},
            headers=NOMINATIM_HEADERS,
            timeout=20.0,
        )
        r.raise_for_status()
        raw = r.json() or []
    except Exception as e:
        logger.warning("[LISTINGS] Nominatim geocoder failed: %r", e)
        return []
    out: List[Dict[str, Any]] = []
    for m in raw:
        try:
            out.append({"lat": float(m["lat"]), "lng": float(m["lon"]), "matched_address": m.get("display_name") or addr, "source": "nominatim"})
        except (KeyError, TypeError, ValueError):
            continue
    return out


@router.post("/geocode")
async def geocode_address(payload: GeocodeRequest, current_user: dict = Depends(get_current_user)):
    """Resolve a U.S. address to lat/lng. Tries the free Census geocoder first,
    then falls back to OpenStreetMap/Nominatim when Census returns nothing. Returns
    the best match at the top level (back-compat) plus a `matches` list so the UI
    can offer a 'did you mean' picker. 404 only when BOTH providers find nothing."""
    addr = (payload.address or "").strip()
    if not addr:
        raise HTTPException(status_code=400, detail="Address is required.")

    async with httpx.AsyncClient() as client:
        matches = await _census_matches(client, addr)
        if not matches:
            matches = await _nominatim_matches(client, addr)

    if not matches:
        raise HTTPException(status_code=404, detail=f"Address not found: {addr}. Try adding the city, state, or ZIP code.")

    best = matches[0]
    return {**best, "matches": matches}


@router.post("/geocode/reverse")
async def reverse_geocode(payload: ReverseGeocodeRequest, current_user: dict = Depends(get_current_user)):
    """lat/lng -> a human address (for the draggable map pin). Uses Nominatim. The
    point is authoritative; we only resolve a label, so failure returns the coords
    with an empty label instead of erroring."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                NOMINATIM_REVERSE_URL,
                params={"lat": payload.latitude, "lon": payload.longitude, "format": "json"},
                headers=NOMINATIM_HEADERS,
                timeout=20.0,
            )
            r.raise_for_status()
            data = r.json() or {}
    except Exception as e:
        logger.warning("[LISTINGS] Nominatim reverse failed: %r", e)
        data = {}
    return {
        "lat": payload.latitude,
        "lng": payload.longitude,
        "matched_address": data.get("display_name") or "",
        "source": "nominatim",
    }


class CompsRequest(BaseModel):
    # Either address OR (latitude+longitude) must be set. Subject attributes
    # narrow the comp set (RentCast picks comps closer to these traits).
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    property_type: Optional[str] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[float] = None
    square_footage: Optional[int] = None
    radius: Optional[float] = None       # miles, defaults to RentCast's default (5)
    days_old: Optional[int] = None       # only comps sold within N days
    comp_count: Optional[int] = None     # number of comps to return (max 25)


def _normalize_comp(c: Dict[str, Any]) -> Dict[str, Any]:
    """RentCast comparable property → compact UI shape."""
    return {
        "id": c.get("id") or c.get("formattedAddress"),
        "address": c.get("formattedAddress") or c.get("addressLine1"),
        "city": c.get("city"),
        "state": c.get("state"),
        "zip": c.get("zipCode"),
        "lat": c.get("latitude"),
        "lng": c.get("longitude"),
        "property_type": c.get("propertyType"),
        "bedrooms": c.get("bedrooms"),
        "bathrooms": c.get("bathrooms"),
        "sqft": c.get("squareFootage"),
        "lot_size": c.get("lotSize"),
        "year_built": c.get("yearBuilt"),
        "price": c.get("price"),
        # RentCast names it "listingType" — "Sale" / "Standard Sale" usually.
        "listing_type": c.get("listingType"),
        "listed_date": c.get("listedDate"),
        "removed_date": c.get("removedDate"),
        "last_seen_date": c.get("lastSeenDate"),
        "days_on_market": c.get("daysOnMarket"),
        "distance": c.get("distance"),       # miles
        "correlation": c.get("correlation"), # 0..1, RentCast's "how similar"
    }


# Tiny demo subject + comps so the comps button works without RENTCAST_API_KEY.
_DEMO_COMPS_BASE = {
    "value": 712_000,
    "value_low": 670_000,
    "value_high": 755_000,
    "lat": 32.748,
    "lng": -117.097,
}
_DEMO_COMPS_ROWS: List[Dict[str, Any]] = [
    {"id": "comp-1", "address": "3760 Marlborough Ave, San Diego, CA 92105",
     "city": "San Diego", "state": "CA", "zip": "92105", "lat": 32.749, "lng": -117.099,
     "property_type": "Single Family", "bedrooms": 3, "bathrooms": 2, "sqft": 1280,
     "lot_size": 5800, "year_built": 1951, "price": 705_000, "listing_type": "Sale",
     "removed_date": "2026-04-12", "distance": 0.10, "correlation": 0.91},
    {"id": "comp-2", "address": "3890 Marlborough Ave, San Diego, CA 92105",
     "city": "San Diego", "state": "CA", "zip": "92105", "lat": 32.751, "lng": -117.096,
     "property_type": "Single Family", "bedrooms": 3, "bathrooms": 2, "sqft": 1340,
     "lot_size": 6200, "year_built": 1955, "price": 720_000, "listing_type": "Sale",
     "removed_date": "2026-05-03", "distance": 0.16, "correlation": 0.88},
    {"id": "comp-3", "address": "4012 Polk Ave, San Diego, CA 92105",
     "city": "San Diego", "state": "CA", "zip": "92105", "lat": 32.746, "lng": -117.094,
     "property_type": "Single Family", "bedrooms": 3, "bathrooms": 1.5, "sqft": 1180,
     "lot_size": 5400, "year_built": 1948, "price": 668_000, "listing_type": "Sale",
     "removed_date": "2026-03-22", "distance": 0.28, "correlation": 0.82},
]


@router.post("/comps")
async def fetch_comps(payload: CompsRequest, current_user: dict = Depends(get_current_user)):
    """Return RentCast's AVM value + comparable sale properties for a subject.
    Provide an address OR lat/lng; subject traits (beds/baths/sqft) narrow the
    comp pool. Falls back to demo data when RENTCAST_API_KEY is unset so the
    UI can be developed against the same shape."""
    api_key = os.getenv("RENTCAST_API_KEY")
    if not (payload.address or (payload.latitude is not None and payload.longitude is not None)):
        raise HTTPException(
            status_code=400,
            detail="Provide an address or a lat+lng for the subject property.",
        )

    if not api_key:
        return {
            **_DEMO_COMPS_BASE,
            "comps": _DEMO_COMPS_ROWS,
            "source": "demo",
            "note": "Demo comps — set RENTCAST_API_KEY on the server to pull live data.",
        }

    params: Dict[str, Any] = {}
    if payload.address:
        params["address"] = payload.address
    if payload.latitude is not None and payload.longitude is not None:
        params["latitude"] = payload.latitude
        params["longitude"] = payload.longitude
    if payload.property_type:
        params["propertyType"] = payload.property_type
    if payload.bedrooms is not None:
        params["bedrooms"] = payload.bedrooms
    if payload.bathrooms is not None:
        params["bathrooms"] = payload.bathrooms
    if payload.square_footage is not None:
        params["squareFootage"] = payload.square_footage
    if payload.radius is not None and payload.radius > 0:
        params["radius"] = payload.radius
    if payload.days_old is not None and payload.days_old > 0:
        params["daysOld"] = payload.days_old
    if payload.comp_count is not None:
        params["compCount"] = max(1, min(25, int(payload.comp_count)))

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{RENTCAST_BASE}/avm/value",
                params=params,
                headers={"X-Api-Key": api_key, "Accept": "application/json"},
                timeout=RENTCAST_TIMEOUT,
            )
    except Exception as e:
        logger.error("[LISTINGS] RentCast AVM request failed: %r", e)
        raise HTTPException(status_code=502, detail=f"Comps provider error: {e}")

    if r.status_code == 401:
        raise HTTPException(status_code=502, detail="Comps provider rejected the API key (401).")
    if r.status_code == 404:
        # RentCast 404 usually means "no record / not enough comps in coverage."
        raise HTTPException(status_code=404, detail="No comps found for this subject.")
    if r.status_code == 429:
        raise HTTPException(status_code=429, detail="Comps provider quota exceeded. Try again later.")
    if r.status_code >= 400:
        logger.warning("[LISTINGS] RentCast AVM %s: %s", r.status_code, r.text[:300])
        raise HTTPException(status_code=502, detail=f"Comps provider returned {r.status_code}.")

    data = r.json() if r.content else {}
    comparables = data.get("comparables") or []
    return {
        "value": data.get("price"),
        "value_low": data.get("priceRangeLow"),
        "value_high": data.get("priceRangeHigh"),
        "lat": data.get("latitude"),
        "lng": data.get("longitude"),
        "comps": [_normalize_comp(c) for c in comparables],
        "source": "rentcast",
    }


# ============================================================
# Property records (RentCast /properties) — owner, owner-occupancy, last sale,
# tax assessment + annual taxes, HOA, features. Owner data is name + MAILING
# ADDRESS only (RentCast does NOT expose owner phone/email — that's skip tracing,
# a separate future integration). Useful here to flag absentee owners and gauge
# the seller's basis (what they paid) for a flip.
# ============================================================

class PropertyRecordRequest(BaseModel):
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


def _latest_year(d: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(d, dict) or not d:
        return None
    try:
        return max(d.keys())
    except (ValueError, TypeError):
        return None


def _normalize_property(p: Dict[str, Any]) -> Dict[str, Any]:
    """RentCast property record → compact UI shape. Surfaces owner (name + mailing
    address + entity type), owner-occupancy, last sale, latest tax assessment +
    annual property tax, HOA fee, and the raw features bag."""
    owner = p.get("owner") if isinstance(p.get("owner"), dict) else {}
    mailing = owner.get("mailingAddress") if isinstance(owner.get("mailingAddress"), dict) else None
    assessments = p.get("taxAssessments") if isinstance(p.get("taxAssessments"), dict) else {}
    taxes = p.get("propertyTaxes") if isinstance(p.get("propertyTaxes"), dict) else {}
    a_year = _latest_year(assessments)
    t_year = _latest_year(taxes)
    hoa = p.get("hoa") if isinstance(p.get("hoa"), dict) else None
    return {
        "address": p.get("formattedAddress") or p.get("addressLine1"),
        "lat": p.get("latitude"),
        "lng": p.get("longitude"),
        "property_type": p.get("propertyType"),
        "bedrooms": p.get("bedrooms"),
        "bathrooms": p.get("bathrooms"),
        "sqft": p.get("squareFootage"),
        "lot_size": p.get("lotSize"),
        "year_built": p.get("yearBuilt"),
        "owner": {
            "names": owner.get("names") or [],
            "type": owner.get("type"),
            "mailing_address": (mailing.get("formattedAddress") if mailing else None),
        } if owner else None,
        "owner_occupied": p.get("ownerOccupied"),
        "last_sale_price": p.get("lastSalePrice"),
        "last_sale_date": p.get("lastSaleDate"),
        "hoa_fee": (hoa.get("fee") if hoa else None),
        "tax_assessment": ({"year": a_year, "value": (assessments.get(a_year) or {}).get("value")} if a_year else None),
        "property_tax": ({"year": t_year, "total": (taxes.get(t_year) or {}).get("total")} if t_year else None),
        "features": p.get("features") or None,
        "source": "rentcast",
    }


_DEMO_PROPERTY: Dict[str, Any] = {
    "address": "3812 Marlborough Ave, San Diego, CA 92105",
    "lat": 32.748, "lng": -117.097,
    "property_type": "Single Family", "bedrooms": 3, "bathrooms": 2, "sqft": 1320,
    "lot_size": 6000, "year_built": 1952,
    "owner": {"names": ["Maria T Gonzalez"], "type": "Individual", "mailing_address": "PO Box 1182, Chula Vista, CA 91912"},
    "owner_occupied": False,
    "last_sale_price": 415000, "last_sale_date": "2014-08-21",
    "hoa_fee": None,
    "tax_assessment": {"year": "2025", "value": 498000},
    "property_tax": {"year": "2025", "total": 5840},
    "features": {"architectureType": "Bungalow", "garage": True, "pool": False, "roofType": "Composition"},
    "source": "demo",
    "note": "Demo property record — set RENTCAST_API_KEY on the server to pull live data.",
}


@router.post("/property")
async def fetch_property_record(payload: PropertyRecordRequest, current_user: dict = Depends(get_current_user)):
    """Return a RentCast property record for an address (or lat+lng). Falls back to
    a demo record when RENTCAST_API_KEY is unset."""
    api_key = os.getenv("RENTCAST_API_KEY")
    if not (payload.address or (payload.latitude is not None and payload.longitude is not None)):
        raise HTTPException(status_code=400, detail="Provide an address or a lat+lng.")

    if not api_key:
        return dict(_DEMO_PROPERTY)

    params: Dict[str, Any] = {}
    if payload.address:
        params["address"] = payload.address
    if payload.latitude is not None and payload.longitude is not None:
        params["latitude"] = payload.latitude
        params["longitude"] = payload.longitude

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{RENTCAST_BASE}/properties",
                params=params,
                headers={"X-Api-Key": api_key, "Accept": "application/json"},
                timeout=RENTCAST_TIMEOUT,
            )
    except Exception as e:
        logger.error("[LISTINGS] RentCast properties request failed: %r", e)
        raise HTTPException(status_code=502, detail=f"Property provider error: {e}")

    if r.status_code == 401:
        raise HTTPException(status_code=502, detail="Property provider rejected the API key (401).")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="No property record found for this address.")
    if r.status_code == 429:
        raise HTTPException(status_code=429, detail="Property provider quota exceeded. Try again later.")
    if r.status_code >= 400:
        logger.warning("[LISTINGS] RentCast properties %s: %s", r.status_code, r.text[:300])
        raise HTTPException(status_code=502, detail=f"Property provider returned {r.status_code}.")

    data = r.json() if r.content else []
    items = data if isinstance(data, list) else (data.get("data") or [data])
    if not items:
        raise HTTPException(status_code=404, detail="No property record found for this address.")
    return _normalize_property(items[0])
