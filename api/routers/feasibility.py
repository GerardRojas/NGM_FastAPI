# api/routers/feasibility.py
# ================================
# Feasibility Study API Router
# ================================
# Phase 1: parcel + zoning lookup for the Feasibility Study module.
#
# Given an address or APN, this aggregates real City of San Diego / SanGIS GIS
# data server-side (avoids browser CORS + lets us add caching later) and returns
# a normalized parcel record plus, when available, the zoning standards seeded in
# the `zoning_standards` table.
#
# Data sources (verified live 2026-05-27):
#   - Geocoder: City of San Diego DSD Accela locator (address -> x/y)
#   - Parcels:  SanGIS/SANDAG hosted Parcels FeatureServer (APN, situs, geometry)
#   - Zoning:   City of San Diego DSD Official Zoning Map (ZONE_NAME)
#
# Everything is intersected in EPSG:4326 (lat/lng); parcel geometry is requested
# in EPSG:2230 (CA State Plane Zone 6, US feet) so lot area comes out directly in
# square feet via the shoelace formula (the `acreage` attribute is often null).

import re
import asyncio
import logging
from typing import Optional, List, Dict, Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from api.auth import get_current_user
from api.supabase_client import supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feasibility", tags=["Feasibility"])

# ====== GIS ENDPOINTS ======

GEOCODER_URL = (
    "https://webmaps.sandiego.gov/arcgis/rest/services/DSD/"
    "Accela_Locator/GeocodeServer/findAddressCandidates"
)
# Free U.S. Census geocoder, used as the county-wide fallback when the City of
# SD DSD locator does not match (e.g. addresses in Chula Vista, Encinitas, or
# unincorporated communities like Jamul). No API key needed.
CENSUS_GEOCODER_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"

PARCELS_URL = (
    "https://geo.sandag.org/server/rest/services/Hosted/"
    "Parcels/FeatureServer/0/query"
)
# City of San Diego zoning (Ch.13 base zones + planned districts in Ch.15).
ZONING_URL = (
    "https://webmaps.sandiego.gov/arcgis/rest/services/DSD/"
    "Zoning_Base/MapServer/0/query"
)
# County of San Diego Planning & Development Services zoning, for parcels in
# unincorporated communities (Ramona, Jamul, Alpine, Fallbrook, Spring Valley,
# Bonita, etc.). Verify the URL is still live on first deploy — if PDS reorgs
# their REST endpoints, point this at the new path; the graceful-degradation
# fallback below will turn a bad URL into a warning rather than a hard 502.
COUNTY_ZONING_URL = (
    "https://gis.sandiegocounty.gov/arcgis/rest/services/PDS/"
    "Zoning/MapServer/0/query"
)

GIS_TIMEOUT = 25.0

# City of San Diego Chapter 13 base-zone prefixes. Anything that does not start
# with one of these (e.g. "CCPD-ER" = Centre City Planned District, Ch.15) is
# treated as a planned district whose standards live in a separate code section.
BASE_ZONE_PREFIXES = (
    "RS-", "RM-", "RX-", "RT-", "RE-", "RD-",
    "CC-", "CN-", "CV-", "CO-", "CP-", "CR-",
    "IL-", "IH-", "IP-", "IS-", "IBT-",
    "OC-", "OF-", "OP-", "OR-",
    "AG-", "AR-",
)

WEBMAPS = "https://webmaps.sandiego.gov/arcgis/rest/services"

# Incorporated cities in San Diego County OTHER than the City of San Diego. A
# parcel whose situs_community matches one of these is in an incorporated city
# whose zoning we don't yet automate — the lookup still returns parcel + APN
# + lot data, but zoning/standards come back null with a clear warning. Add a
# city here only after wiring its zoning MapServer.
INCORPORATED_CITIES_NON_SD = frozenset({
    "CHULA VISTA", "OCEANSIDE", "ESCONDIDO", "CARLSBAD", "EL CAJON",
    "VISTA", "SAN MARCOS", "ENCINITAS", "NATIONAL CITY", "LA MESA",
    "SANTEE", "POWAY", "CORONADO", "IMPERIAL BEACH", "LEMON GROVE",
    "DEL MAR", "SOLANA BEACH",
})

# Where a parcel falls in the SD County jurisdictional hierarchy. Drives which
# zoning service we hit and which sections of DEFAULT_REGULATIONS apply (only
# the city_san_diego regs are seeded by jurisdiction today).
Jurisdiction = Literal["city_san_diego", "county_unincorporated", "other_city", "unknown"]


def _jurisdiction_for(community: Optional[str]) -> Jurisdiction:
    """Map a parcel's situs_community to its jurisdictional bucket. Anything
    that isn't the City of SD nor one of the other 17 incorporated cities is
    treated as unincorporated county territory (Ramona, Jamul, Alpine, etc.)."""
    if not community:
        return "unknown"
    c = community.strip().upper()
    if c == "SAN DIEGO":
        return "city_san_diego"
    if c in INCORPORATED_CITIES_NON_SD:
        return "other_city"
    return "county_unincorporated"

# Constraint catalog. Every layer is hosted by the City of San Diego (reliable;
# avoids the FEMA/CAL-FIRE TLS + timeout problems) and queried as a point
# intersect. `service` groups layers for per-source status reporting.
#   tone:     positive | neutral | negative  (development impact when present)
#   severity: info | warn | high             (only meaningful when present)
CONSTRAINT_LAYERS: List[Dict[str, Any]] = [
    # ── Overlays (Zoning_Overlay) ─────────────────────────────────────────
    {"key": "coastal_overlay", "label": "Coastal Overlay Zone", "category": "Coastal",
     "service": "overlays", "url": f"{WEBMAPS}/DSD/Zoning_Overlay/MapServer/2",
     "tone": "negative", "severity": "warn",
     "note": "Coastal Overlay Zone - a Coastal Development Permit (CDP) is required"},
    {"key": "coastal_height", "label": "Coastal Height Limitation", "category": "Coastal",
     "service": "overlays", "url": f"{WEBMAPS}/DSD/Zoning_Overlay/MapServer/1",
     "tone": "negative", "severity": "warn",
     "note": "Coastal height limit (30 ft) applies"},
    {"key": "transit_area", "label": "Transit Area Overlay", "category": "Transit",
     "service": "overlays", "url": f"{WEBMAPS}/DSD/Zoning_Overlay/MapServer/9",
     "tone": "positive", "severity": "info",
     "note": "Transit Area Overlay - reduced parking and density incentives may apply"},
    {"key": "parking_impact", "label": "Parking Impact Overlay", "category": "Parking",
     "service": "overlays", "url": f"{WEBMAPS}/DSD/Zoning_Overlay/MapServer/7",
     "tone": "neutral", "severity": "info",
     "note": "Parking Impact Overlay (Beach/Campus) - special parking standards"},
    {"key": "cpioz", "label": "Community Plan Implementation Overlay", "category": "Planning",
     "service": "overlays", "url": f"{WEBMAPS}/DSD/Zoning_Overlay/MapServer/3",
     "tone": "neutral", "severity": "info",
     "note": "Community Plan Implementation Overlay - supplemental development regulations"},
    # ── Environmentally Sensitive Lands (Environment) ─────────────────────
    {"key": "fault_ap", "label": "Alquist-Priolo Fault Zone", "category": "Seismic",
     "service": "environment", "url": f"{WEBMAPS}/DSD/Environment/MapServer/1",
     "tone": "negative", "severity": "high",
     "note": "Alquist-Priolo Earthquake Fault Zone - a fault-rupture study is required before construction"},
    {"key": "fault_buffer", "label": "Earthquake Fault Buffer", "category": "Seismic",
     "service": "environment", "url": f"{WEBMAPS}/DSD/Environment/MapServer/8",
     "tone": "negative", "severity": "warn",
     "note": "Earthquake Fault Buffer - geotechnical review required"},
    {"key": "geo_hazard", "label": "Geologic Hazard", "category": "Geologic",
     "service": "environment", "url": f"{WEBMAPS}/DSD/Environment/MapServer/10",
     "tone": "negative", "severity": "warn",
     "note": "Mapped geologic hazard - a geotechnical investigation is likely required"},
    {"key": "steep_slope", "label": "Steep Slopes (>=25%)", "category": "Topography",
     "service": "environment", "url": f"{WEBMAPS}/DSD/Environment/MapServer/16",
     "tone": "negative", "severity": "warn",
     "note": "Steep slopes (25% or greater) - ESL regulations reduce buildable area; a deviation may be needed"},
    {"key": "wetlands", "label": "Non-Coastal Wetlands", "category": "Biology",
     "service": "environment", "url": f"{WEBMAPS}/DSD/Environment/MapServer/13",
     "tone": "negative", "severity": "high",
     "note": "Non-coastal wetlands - habitat impact review; development may be precluded"},
    {"key": "mhpa", "label": "Multiple Habitat Planning Area", "category": "Biology",
     "service": "environment", "url": f"{WEBMAPS}/DSD/Environment/MapServer/12",
     "tone": "negative", "severity": "high",
     "note": "MSCP Multiple Habitat Planning Area - significant biological constraints"},
    {"key": "sensitive_veg", "label": "Sensitive Vegetation", "category": "Biology",
     "service": "environment", "url": f"{WEBMAPS}/DSD/Environment/MapServer/15",
     "tone": "negative", "severity": "warn",
     "note": "Sensitive vegetation - a biological survey may be required"},
    {"key": "vernal_pools", "label": "Vernal Pools", "category": "Biology",
     "service": "environment", "url": f"{WEBMAPS}/DSD/Environment/MapServer/19",
     "tone": "negative", "severity": "high",
     "note": "Vernal pools - protected resource; development is likely precluded"},
    # ── Fire (Fire) ───────────────────────────────────────────────────────
    {"key": "vhfhsz", "label": "Very High Fire Hazard Severity Zone", "category": "Fire",
     "service": "fire", "url": f"{WEBMAPS}/DSD/Fire/MapServer/6",
     "tone": "negative", "severity": "high",
     "note": "Very High Fire Hazard Severity Zone - WUI building codes and brush management apply"},
    {"key": "brush_mgmt", "label": "Brush Management Zone", "category": "Fire",
     "service": "fire", "url": f"{WEBMAPS}/DSD/Fire/MapServer/4",
     "tone": "negative", "severity": "warn",
     "note": "Brush Management zone - defensible-space requirements apply"},
    # ── Transit Priority Area (Planning) ──────────────────────────────────
    {"key": "tpa", "label": "Transit Priority Area", "category": "Transit",
     "service": "planning", "url": f"{WEBMAPS}/Planning/PLN_TransitPriorityArea/MapServer/0",
     "tone": "positive", "severity": "info",
     "note": "Within a Transit Priority Area - parking minimums eliminated (AB 2097); density bonus and CEQA/VMT benefits"},
    # ── Flood (Regulatory) - custom interpretation in _interpret_flood ─────
    {"key": "flood", "label": "FEMA Flood Zone", "category": "Flood",
     "service": "regulatory", "url": f"{WEBMAPS}/DSD/Regulatory/MapServer/10",
     "tone": "neutral", "severity": "info", "flood": True,
     "note": "FEMA flood zone"},
]

# FEMA Special Flood Hazard Area zone codes (mandatory insurance + elevation).
SFHA_ZONES = {"A", "AE", "AH", "AO", "AR", "A99", "V", "VE", "A1-A30", "VO"}

# Default regulatory ruleset (drives the client yield engine + the "Regulatory
# Basis" UI card). Stored in the `feasibility_regulations` table so it can be
# updated as the City/State change the rules without a code deploy; this dict is
# the seed + fallback. Verified 2026-05-27; SD ADU Bonus Program reforms took
# effect 2025-08-22 (coastal zone pending Coastal Commission LCP cert ~2026).
DEFAULT_REGULATIONS: Dict[str, Any] = {
    "version": "2026-05-27",
    "last_verified": "2026-05-27",
    "jurisdiction": "City of San Diego",
    "adu": {
        "state_byright_sf": {"adu": 1, "jadu": 1, "detached": 1, "detached_max_sf": 800},
        "state_byright_mf": {"conversion_pct": 0.25, "detached_max": 8},
        "bonus_program": {
            "enabled": True,
            "lot_caps": [
                {"max_lot_sf": 8000, "cap": 4},
                {"max_lot_sf": 10000, "cap": 5},
                {"max_lot_sf": None, "cap": 6},
            ],
            "outside_sda_bonus": 1,
            "affordability_term_years": 15,
            "excluded_zones": ["RS-1-1", "RS-1-2", "RS-1-3", "RS-1-4", "RS-1-8", "RS-1-9", "RS-1-10", "RS-1-11"],
            "coastal_effective": False,
            "far_lot_area_cap_sf": 8000,
        },
        "parking": {"required_per_bonus_outside_tpa": 1, "transit_waiver_mi": 0.5},
    },
    "density_bonus": {"tiers": [
        {"min_affordable": 15, "bonus": 50},
        {"min_affordable": 10, "bonus": 35},
        {"min_affordable": 5, "bonus": 20},
    ]},
    "ab2011": {"floor_density_base": 40, "floor_density_tpa": 80},
    "sb9": {"max_units": 4},
    "sources": [
        {"rule": "ADU / JADU (state by-right)", "citation": "Cal. Gov. Code 66310-66342 (66323)", "url": "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?sectionNum=66323&lawCode=GOV", "effective": "2024-01-01", "note": "1 ADU + 1 JADU + 1 detached (<=800 sf) on a single-family lot."},
        {"rule": "San Diego ADU Bonus Program", "citation": "SDMC 141.0302", "url": "https://docs.sandiego.gov/municode/municodechapter14/ch14art01division03.pdf", "effective": "2025-08-22", "note": "Lot-size caps 4/5/6; 1 market bonus per affordable inside an SDA. Coastal zone pending LCP certification (~2026)."},
        {"rule": "ADU/JADU (City info)", "citation": "Information Bulletin 400", "url": "https://www.sandiego.gov/development-services/forms-publications/information-bulletins/400", "effective": "2026-01-01", "note": None},
        {"rule": "SB 9 (lot split + duplex)", "citation": "Cal. Gov. Code 65852.21 / 66411.7", "url": "https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202120220SB9", "effective": "2022-01-01", "note": None},
        {"rule": "Density Bonus + AB 1287", "citation": "Cal. Gov. Code 65915", "url": "https://codes.findlaw.com/ca/government-code/gov-sect-65915/", "effective": "2024-01-01", "note": None},
        {"rule": "AB 2011 (commercial corridors)", "citation": "Cal. Gov. Code 65912.100+", "url": "https://leginfo.legislature.ca.gov/faces/codes_displayexpandedbranch.xhtml?lawCode=GOV&division=1.&title=7.&part=&chapter=4.1.", "effective": "2023-07-01", "note": None},
        {"rule": "AB 2097 (parking near transit)", "citation": "Cal. Gov. Code 65863.2", "url": "https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202120220AB2097", "effective": "2023-01-01", "note": None},
        {"rule": "SB 35 / SB 423 (streamlining)", "citation": "Cal. Gov. Code 65913.4", "url": "https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202320240SB423", "effective": "2024-01-01", "note": "Requires the City to be behind its RHNA - verify HCD status."},
    ],
}


# ====== MODELS ======

class LookupRequest(BaseModel):
    address: Optional[str] = None
    apn: Optional[str] = None


class GisSource(BaseModel):
    name: str
    ok: bool
    detail: Optional[str] = None


class ParcelRecord(BaseModel):
    apn: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    zip: Optional[str] = None
    county: str = "San Diego"
    # Jurisdictional bucket — drives which zoning service this record came from
    # and whether DEFAULT_REGULATIONS (City-of-SD-only today) applies. Other-city
    # parcels return with zoning=None and an explanatory warning.
    jurisdiction: Jurisdiction = "unknown"
    lot_sf: int = 0
    lot_acres: float = 0.0
    zoning: Optional[str] = None
    zoning_ordinance: Optional[str] = None
    is_planned_district: bool = False
    assessed_total: Optional[float] = None
    assessed_land: Optional[float] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    source: str = "SanGIS + City of San Diego DSD"


class LookupResponse(BaseModel):
    parcel: ParcelRecord
    zoning_standards: Optional[Dict[str, Any]] = None
    warnings: List[str] = []
    sources: List[GisSource] = []


class ConstraintsRequest(BaseModel):
    lat: float
    lng: float


class ConstraintResult(BaseModel):
    key: str
    label: str
    category: str
    present: bool
    tone: str          # positive | neutral | negative
    severity: str      # info | warn | high | na
    note: Optional[str] = None
    value: Optional[str] = None
    source_url: str


class ConstraintsResponse(BaseModel):
    constraints: List[ConstraintResult]
    summary: Dict[str, int]
    sources: List[GisSource] = []


class DealCreate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    apn: Optional[str] = None
    zoning: Optional[str] = None
    decision: Optional[str] = None
    total_uses: Optional[float] = None
    irr: Optional[float] = None
    max_units: Optional[int] = None
    data: Dict[str, Any]            # full analysis snapshot (parcel, zone, proforma, etc.)


# ====== HELPERS ======

def _is_planned_district(zone: Optional[str]) -> bool:
    if not zone:
        return False
    z = zone.strip().upper()
    if z.startswith(tuple(p.upper() for p in BASE_ZONE_PREFIXES)):
        return False
    # Common planned-district / non-base markers.
    return True


def _shoelace_sqft(rings: List[List[List[float]]]) -> float:
    """Area (sq ft) of a polygon whose coordinates are already in feet (EPSG:2230).
    Sums outer ring(s); inner rings (holes) are subtracted by ArcGIS ring winding,
    but parcels are simple polygons so we sum the absolute area of each ring."""
    total = 0.0
    for ring in rings:
        s = 0.0
        for i in range(len(ring) - 1):
            s += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
        total += abs(s) / 2.0
    return total


async def _geocode_dsd(client: httpx.AsyncClient, address: str) -> Optional[Dict[str, Any]]:
    """City of San Diego DSD Accela locator. Best precision inside city limits;
    returns a 0–100 score that we threshold for warnings. Returns None when no
    candidate is found (caller falls back to Census)."""
    params = {
        "SingleLine": address,
        "outSR": 4326,
        "maxLocations": 1,
        "f": "json",
    }
    r = await client.get(GEOCODER_URL, params=params, timeout=GIS_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    candidates = data.get("candidates") or []
    if not candidates:
        return None
    loc = candidates[0].get("location") or {}
    if "x" not in loc or "y" not in loc:
        return None
    return {
        "lng": loc["x"],
        "lat": loc["y"],
        "matched": candidates[0].get("address"),
        "score": candidates[0].get("score", 0),
        "source": "dsd",
    }


async def _geocode_census(client: httpx.AsyncClient, address: str) -> Optional[Dict[str, Any]]:
    """U.S. Census geocoder. County-wide (actually U.S.-wide), no API key,
    slightly slower than DSD. Used as the fallback so the tool can resolve
    addresses outside the City of SD (Chula Vista, Encinitas, unincorporated
    communities, etc.). Census has no confidence score — exact-match output is
    treated as ~95 so the low-confidence warning only fires for true low-score
    DSD matches."""
    params = {
        "address": address,
        "benchmark": "Public_AR_Current",
        "format": "json",
    }
    r = await client.get(CENSUS_GEOCODER_URL, params=params, timeout=GIS_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    matches = ((data.get("result") or {}).get("addressMatches")) or []
    if not matches:
        return None
    m = matches[0]
    coords = m.get("coordinates") or {}
    if "x" not in coords or "y" not in coords:
        return None
    return {
        "lng": coords["x"],
        "lat": coords["y"],
        "matched": m.get("matchedAddress"),
        "score": 95,
        "source": "census",
    }


async def _geocode_address(client: httpx.AsyncClient, address: str) -> Optional[Dict[str, Any]]:
    """Cascade geocoder: try DSD first (best for in-city addresses), fall back
    to U.S. Census for county-wide coverage. Returns the first hit with its
    `source` tagged so the caller can record it in GisSource."""
    try:
        dsd = await _geocode_dsd(client, address)
        if dsd:
            return dsd
    except Exception as e:
        logger.warning("[FEASIBILITY] DSD geocoder error, falling back to Census: %r", e)
    try:
        census = await _geocode_census(client, address)
        if census:
            return census
    except Exception as e:
        logger.warning("[FEASIBILITY] Census geocoder error: %r", e)
    return None


# Backwards-compat shim: `_geocode` was the DSD-only function. Anything that
# imported it externally still gets the same shape. New code should call
# `_geocode_address` for the cascade behavior.
_geocode = _geocode_dsd


async def _parcel_by_point(client: httpx.AsyncClient, lng: float, lat: float) -> Optional[Dict[str, Any]]:
    params = {
        "geometry": f"{lng},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,
        "outSR": 2230,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "apn,situs_street,situs_pre_dir,situs_post_dir,situs_community,situs_zip,acreage,asr_total,asr_land",
        "returnGeometry": "true",
        "resultRecordCount": 1,
        "f": "json",
    }
    r = await client.get(PARCELS_URL, params=params, timeout=GIS_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    feats = data.get("features") or []
    return feats[0] if feats else None


async def _parcel_by_apn(client: httpx.AsyncClient, apn: str, out_sr: int = 2230) -> Optional[Dict[str, Any]]:
    clean = re.sub(r"[^0-9]", "", apn)
    params = {
        "where": f"apn='{clean}'",
        "outSR": out_sr,
        "outFields": "apn,situs_street,situs_pre_dir,situs_post_dir,situs_community,situs_zip,acreage,asr_total,asr_land",
        "returnGeometry": "true",
        "resultRecordCount": 1,
        "f": "json",
    }
    r = await client.get(PARCELS_URL, params=params, timeout=GIS_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    feats = data.get("features") or []
    return feats[0] if feats else None


def _ring_centroid(rings: List[List[List[float]]]) -> Optional[Dict[str, float]]:
    """Average vertex of the first ring, in whatever SR the ring is expressed in
    (good enough to re-query zoning/constraints when we only have a parcel)."""
    if not rings or not rings[0]:
        return None
    ring = rings[0]
    xs = sum(p[0] for p in ring) / len(ring)
    ys = sum(p[1] for p in ring) / len(ring)
    return {"x": xs, "y": ys}


async def _zoning_at(
    client: httpx.AsyncClient,
    geometry: str,
    in_sr: int,
    *,
    url: str = ZONING_URL,
    out_fields: str = "ZONE_NAME,ORDNUM",
) -> Optional[Dict[str, Any]]:
    """Point-intersect query against an ArcGIS zoning MapServer. Defaults to
    the City of San Diego service; callers in unincorporated/other-city paths
    pass their own URL + outFields. Returns the matching feature's attributes
    dict (or None if no feature intersects the point)."""
    params = {
        "geometry": geometry,
        "geometryType": "esriGeometryPoint",
        "inSR": in_sr,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": out_fields,
        "returnGeometry": "false",
        "f": "json",
    }
    r = await client.get(url, params=params, timeout=GIS_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    feats = data.get("features") or []
    return feats[0]["attributes"] if feats else None


def _normalize_zone_attrs(attrs: Optional[Dict[str, Any]], jurisdiction: Jurisdiction) -> Dict[str, Optional[str]]:
    """Each zoning service uses different output field names; normalize them
    to {zone, ordinance} so downstream code (ParcelRecord, standards lookup)
    doesn't have to know which jurisdiction it came from."""
    if not attrs:
        return {"zone": None, "ordinance": None}
    if jurisdiction == "city_san_diego":
        return {"zone": attrs.get("ZONE_NAME"), "ordinance": attrs.get("ORDNUM")}
    # County PDS Zoning typically exposes ZONE / USE_REGS; tolerate variants.
    return {
        "zone": attrs.get("ZONE") or attrs.get("ZONING") or attrs.get("USE_REGS"),
        "ordinance": attrs.get("ORD_NUM") or attrs.get("ORDNUM"),
    }


def _fetch_standards(zone: str) -> Optional[Dict[str, Any]]:
    try:
        res = (
            supabase.table("zoning_standards")
            .select("*")
            .eq("zone_code", zone)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None
    except Exception as e:  # table may not exist yet; degrade gracefully
        logger.warning("[FEASIBILITY] zoning_standards lookup failed: %r", e)
        return None


def _build_address(attrs: Dict[str, Any]) -> str:
    parts = [
        attrs.get("situs_pre_dir") or "",
        attrs.get("situs_street") or "",
        attrs.get("situs_post_dir") or "",
    ]
    return " ".join(p for p in parts if p).strip()


# ====== ENDPOINTS ======

@router.post("/lookup", response_model=LookupResponse)
async def lookup_parcel(payload: LookupRequest, current_user: dict = Depends(get_current_user)):
    """Resolve an address or APN to a real San Diego parcel + zoning record."""
    address = (payload.address or "").strip()
    apn = (payload.apn or "").strip()
    if not address and not apn:
        raise HTTPException(status_code=400, detail="Provide an address or an APN")

    warnings: List[str] = []
    sources: List[GisSource] = []

    async with httpx.AsyncClient() as client:
        lng = lat = None
        parcel_feat: Optional[Dict[str, Any]] = None

        # ── Resolve to a parcel ──────────────────────────────────────────
        if apn:
            try:
                parcel_feat = await _parcel_by_apn(client, apn)
                sources.append(GisSource(name="parcels", ok=parcel_feat is not None))
            except Exception as e:
                sources.append(GisSource(name="parcels", ok=False, detail=str(e)))
                raise HTTPException(status_code=502, detail=f"Parcel service error: {e}")
            if not parcel_feat:
                raise HTTPException(status_code=404, detail=f"No parcel found for APN {apn}")
            # Populate a lat/lng centroid (4326) so downstream constraint lookups
            # have a point to intersect (the primary query is in 2230 for area).
            try:
                pj = await _parcel_by_apn(client, apn, out_sr=4326)
                rings4326 = (pj or {}).get("geometry", {}).get("rings") or []
                c4 = _ring_centroid(rings4326)
                if c4:
                    lng, lat = c4["x"], c4["y"]
            except Exception:
                pass
        else:
            try:
                geo = await _geocode_address(client, address)
                geo_source_name = f"geocoder_{geo.get('source')}" if geo else "geocoder"
                sources.append(GisSource(name=geo_source_name, ok=geo is not None))
            except Exception as e:
                sources.append(GisSource(name="geocoder", ok=False, detail=str(e)))
                raise HTTPException(status_code=502, detail=f"Geocoder error: {e}")
            if not geo:
                raise HTTPException(status_code=404, detail=f"Address not found: {address}")
            lng, lat = geo["lng"], geo["lat"]
            if (geo.get("score") or 0) < 85:
                warnings.append(f"Low geocoder confidence ({geo.get('score')}) for matched address '{geo.get('matched')}'")
            try:
                parcel_feat = await _parcel_by_point(client, lng, lat)
                sources.append(GisSource(name="parcels", ok=parcel_feat is not None))
            except Exception as e:
                sources.append(GisSource(name="parcels", ok=False, detail=str(e)))
                raise HTTPException(status_code=502, detail=f"Parcel service error: {e}")
            if not parcel_feat:
                raise HTTPException(status_code=404, detail="No parcel intersects the geocoded location")

        attrs = parcel_feat.get("attributes", {}) or {}
        rings = (parcel_feat.get("geometry") or {}).get("rings") or []

        # ── Lot size from geometry (feet) ────────────────────────────────
        lot_sf = round(_shoelace_sqft(rings)) if rings else 0
        if not lot_sf and attrs.get("acreage"):
            lot_sf = round(attrs["acreage"] * 43560)
        lot_acres = round(lot_sf / 43560.0, 4) if lot_sf else 0.0

        community = (attrs.get("situs_community") or "").strip()
        jurisdiction = _jurisdiction_for(community)

        # ── Zoning — routed by jurisdiction ──────────────────────────────
        zone_attrs: Optional[Dict[str, Any]] = None
        if jurisdiction == "other_city":
            # Incorporated city we don't yet automate — return parcel data but
            # skip the zoning query entirely. Standards must be entered manually
            # in the UI for now. Add the city's MapServer + a normalize_zone_attrs
            # branch to bring it online.
            sources.append(GisSource(
                name="zoning", ok=False,
                detail=f"Not automated: {community.title()}",
            ))
            warnings.append(
                f"Parcel is in {community.title()} — zoning is not yet automated "
                "for this jurisdiction; standards must be entered manually. "
                "Parcel APN, lot size, and assessed value above are accurate."
            )
        else:
            # city_san_diego → DSD service; county_unincorporated / unknown →
            # County PDS service (covers Ramona, Jamul, Alpine, Fallbrook, etc.).
            if jurisdiction == "city_san_diego":
                z_url, z_fields = ZONING_URL, "ZONE_NAME,ORDNUM"
            else:
                z_url = COUNTY_ZONING_URL
                z_fields = "ZONE,ZONING,USE_REGS,ORD_NUM,ORDNUM"
            try:
                if lng is not None and lat is not None:
                    zone_attrs = await _zoning_at(client, f"{lng},{lat}", 4326, url=z_url, out_fields=z_fields)
                elif rings:
                    c = _ring_centroid(rings)
                    if c:
                        zone_attrs = await _zoning_at(client, f"{c['x']},{c['y']}", 2230, url=z_url, out_fields=z_fields)
                sources.append(GisSource(name="zoning", ok=zone_attrs is not None))
            except Exception as e:
                sources.append(GisSource(name="zoning", ok=False, detail=str(e)))
                warnings.append(f"Zoning service error: {e}")

        zone_norm = _normalize_zone_attrs(zone_attrs, jurisdiction)
        zone = zone_norm["zone"]
        if not zone and jurisdiction in ("city_san_diego", "county_unincorporated"):
            warnings.append("No zoning designation found at this location")
        if jurisdiction == "county_unincorporated" and zone:
            # State laws still apply but the seeded DEFAULT_REGULATIONS ADU bonus
            # program is City-of-SD-specific. Tell the operator so the feasibility
            # engine output is read correctly.
            warnings.append(
                "Unincorporated SD County: state laws (SB 9, AB 2011, AB 2097) "
                "apply, but the City of San Diego ADU Bonus Program does not. "
                "Verify county-specific code requirements."
            )

    is_pd = _is_planned_district(zone) if jurisdiction == "city_san_diego" else False
    record = ParcelRecord(
        apn=attrs.get("apn"),
        address=_build_address(attrs) or None,
        city=community or None,
        zip=attrs.get("situs_zip"),
        jurisdiction=jurisdiction,
        lot_sf=lot_sf,
        lot_acres=lot_acres,
        zoning=zone,
        zoning_ordinance=zone_norm["ordinance"],
        is_planned_district=is_pd,
        assessed_total=attrs.get("asr_total"),
        assessed_land=attrs.get("asr_land"),
        lat=lat,
        lng=lng,
    )

    standards = _fetch_standards(zone) if zone else None
    if zone and not standards:
        msg = f"No seeded zoning standards for '{zone}'"
        if is_pd:
            msg += " (planned district — enter standards manually)"
        warnings.append(msg)

    return LookupResponse(
        parcel=record,
        zoning_standards=standards,
        warnings=warnings,
        sources=sources,
    )


@router.get("/zoning-standards/{zone}")
async def get_zoning_standards(zone: str, current_user: dict = Depends(get_current_user)):
    """Return seeded development standards for a zone code, or 404 if not seeded."""
    standards = _fetch_standards(zone)
    if not standards:
        raise HTTPException(status_code=404, detail=f"No seeded standards for zone '{zone}'")
    return {"data": standards}


# ====== CONSTRAINTS (Phase 2) ======

async def _intersect(client: httpx.AsyncClient, layer_url: str, lng: float, lat: float) -> List[Dict[str, Any]]:
    params = {
        "geometry": f"{lng},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "false",
        "f": "json",
    }
    r = await client.get(f"{layer_url}/query", params=params, timeout=GIS_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(data["error"].get("message", "ArcGIS error"))
    return data.get("features") or []


def _interpret_flood(attrs: Dict[str, Any]) -> Dict[str, Any]:
    """Map a FEMA floodplain feature to a present/severity/tone verdict."""
    zone = (attrs.get("FLD_ZONE") or "").strip()
    sfha = (attrs.get("SFHA_TF") or "").strip().upper()
    is_sfha = sfha == "T" or zone.upper() in SFHA_ZONES
    if is_sfha:
        return {
            "present": True, "tone": "negative", "severity": "high",
            "value": f"Zone {zone}",
            "note": f"Special Flood Hazard Area (Zone {zone}) - flood insurance and elevation requirements apply",
        }
    # Mapped but outside the SFHA (e.g. Zone X) = minimal flood risk.
    return {
        "present": False, "tone": "positive", "severity": "info",
        "value": f"Zone {zone}" if zone else None,
        "note": f"Outside the Special Flood Hazard Area (Zone {zone or 'X'}) - minimal flood risk",
    }


async def _probe(client: httpx.AsyncClient, spec: Dict[str, Any], lng: float, lat: float):
    """Return (ConstraintResult, service_key, ok)."""
    try:
        feats = await _intersect(client, spec["url"], lng, lat)
    except Exception as e:
        logger.warning("[FEASIBILITY] constraint '%s' failed: %r", spec["key"], e)
        return (
            ConstraintResult(
                key=spec["key"], label=spec["label"], category=spec["category"],
                present=False, tone="neutral", severity="na",
                note="Layer unavailable", value=None, source_url=spec["url"],
            ),
            spec["service"], False,
        )

    if spec.get("flood"):
        if not feats:
            verdict = {"present": False, "tone": "neutral", "severity": "na",
                       "value": None, "note": "Not within a mapped FEMA flood area"}
        else:
            verdict = _interpret_flood(feats[0].get("attributes", {}) or {})
        result = ConstraintResult(
            key=spec["key"], label=spec["label"], category=spec["category"],
            present=verdict["present"], tone=verdict["tone"], severity=verdict["severity"],
            note=verdict["note"], value=verdict.get("value"), source_url=spec["url"],
        )
        return (result, spec["service"], True)

    present = len(feats) > 0
    result = ConstraintResult(
        key=spec["key"], label=spec["label"], category=spec["category"],
        present=present,
        tone=spec["tone"] if present else "neutral",
        severity=spec["severity"] if present else "na",
        note=spec["note"] if present else None,
        value=None, source_url=spec["url"],
    )
    return (result, spec["service"], True)


@router.post("/constraints", response_model=ConstraintsResponse)
async def get_constraints(payload: ConstraintsRequest, current_user: dict = Depends(get_current_user)):
    """Intersect a point against the San Diego overlay / ESL / hazard layers.

    All layers are queried concurrently. Each layer is best-effort: a failing
    service is reported in `sources` and never blocks the rest of the analysis.
    """
    lng, lat = payload.lng, payload.lat

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *[_probe(client, spec, lng, lat) for spec in CONSTRAINT_LAYERS]
        )

    constraints = [r for (r, _svc, _ok) in results]

    # Per-service status: a service is ok only if all its layers responded.
    svc_ok: Dict[str, bool] = {}
    for (_r, svc, ok) in results:
        svc_ok[svc] = svc_ok.get(svc, True) and ok
    sources = [GisSource(name=svc, ok=ok) for svc, ok in svc_ok.items()]

    present = [c for c in constraints if c.present]
    summary = {
        "total_present": len(present),
        "high": len([c for c in present if c.severity == "high"]),
        "warn": len([c for c in present if c.severity == "warn"]),
        "positive": len([c for c in present if c.tone == "positive"]),
    }

    return ConstraintsResponse(constraints=constraints, summary=summary, sources=sources)


# ====== SAVED DEALS (Phase 4) ======

@router.post("/deals")
async def create_deal(payload: DealCreate, current_user: dict = Depends(get_current_user)):
    """Persist a feasibility study run for the current user."""
    row = {
        "user_id": str(current_user["user_id"]),
        "name": payload.name,
        "address": payload.address,
        "apn": payload.apn,
        "zoning": payload.zoning,
        "decision": payload.decision,
        "total_uses": payload.total_uses,
        "irr": payload.irr,
        "max_units": payload.max_units,
        "data": payload.data,
    }
    try:
        res = supabase.table("feasibility_deals").insert(row).execute()
    except Exception as e:
        logger.error("[FEASIBILITY] save deal failed: %r", e)
        raise HTTPException(status_code=500, detail=f"Could not save deal: {e}")
    saved = (res.data or [{}])[0]
    return {"id": saved.get("id"), "message": "Deal saved"}


@router.get("/deals")
async def list_deals(current_user: dict = Depends(get_current_user)):
    """List the current user's saved deals (lightweight, newest first)."""
    try:
        res = (
            supabase.table("feasibility_deals")
            .select("id,name,address,apn,zoning,decision,total_uses,irr,max_units,created_at")
            .eq("user_id", str(current_user["user_id"]))
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )
    except Exception as e:
        logger.error("[FEASIBILITY] list deals failed: %r", e)
        raise HTTPException(status_code=500, detail=f"Could not list deals: {e}")
    return {"deals": res.data or []}


@router.get("/deals/{deal_id}")
async def get_deal(deal_id: str, current_user: dict = Depends(get_current_user)):
    """Return one saved deal (including the full analysis snapshot)."""
    try:
        res = (
            supabase.table("feasibility_deals")
            .select("*")
            .eq("id", deal_id)
            .eq("user_id", str(current_user["user_id"]))
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.error("[FEASIBILITY] get deal failed: %r", e)
        raise HTTPException(status_code=500, detail=f"Could not load deal: {e}")
    rows = res.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Deal not found")
    return {"deal": rows[0]}


@router.delete("/deals/{deal_id}")
async def delete_deal(deal_id: str, current_user: dict = Depends(get_current_user)):
    """Delete one of the current user's saved deals."""
    try:
        res = (
            supabase.table("feasibility_deals")
            .delete()
            .eq("id", deal_id)
            .eq("user_id", str(current_user["user_id"]))
            .execute()
        )
    except Exception as e:
        logger.error("[FEASIBILITY] delete deal failed: %r", e)
        raise HTTPException(status_code=500, detail=f"Could not delete deal: {e}")
    if not (res.data or []):
        raise HTTPException(status_code=404, detail="Deal not found")
    return {"message": "Deal deleted"}


# ====== REGULATORY RULESET (data-driven, updatable) ======

class RegulationsUpdate(BaseModel):
    rules: Dict[str, Any]


@router.get("/regulations")
async def get_regulations(current_user: dict = Depends(get_current_user)):
    """Return the active regulatory ruleset (DB row if present, else the default).

    Drives the client yield engine and the 'Regulatory Basis' UI card. Update the
    DB row to reflect a City/State rule change with no code deploy."""
    try:
        res = (
            supabase.table("feasibility_regulations")
            .select("rules")
            .eq("config_key", "main")
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if rows and rows[0].get("rules"):
            return {"data": rows[0]["rules"]}
    except Exception as e:
        logger.warning("[FEASIBILITY] regulations lookup failed, using default: %r", e)
    return {"data": DEFAULT_REGULATIONS}


@router.put("/regulations")
async def update_regulations(payload: RegulationsUpdate, current_user: dict = Depends(get_current_user)):
    """Upsert the regulatory ruleset (admin use). Stamps who/when via updated_at."""
    row = {"config_key": "main", "rules": payload.rules, "updated_at": "now()"}
    try:
        upd = (
            supabase.table("feasibility_regulations")
            .update(row)
            .eq("config_key", "main")
            .execute()
        )
        if not (upd.data or []):
            supabase.table("feasibility_regulations").insert(
                {"config_key": "main", "rules": payload.rules}
            ).execute()
    except Exception as e:
        logger.error("[FEASIBILITY] regulations update failed: %r", e)
        raise HTTPException(status_code=500, detail=f"Could not save regulations: {e}")
    return {"message": "Regulations saved"}
