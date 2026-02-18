"""
QuickBooks Online OAuth2 Service
Handles authentication, token management, and API calls to QBO
"""

import logging
import os
import base64
import httpx
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from api.supabase_client import supabase

logger = logging.getLogger(__name__)


# ====== CONFIGURATION ======

QBO_CLIENT_ID = os.getenv("QBO_CLIENT_ID", "")
QBO_CLIENT_SECRET = os.getenv("QBO_CLIENT_SECRET", "")
QBO_REDIRECT_URI = os.getenv("QBO_REDIRECT_URI", "https://ngm-fastapi.onrender.com/qbo/callback")
QBO_ENVIRONMENT = os.getenv("QBO_ENVIRONMENT", "production")  # "sandbox" or "production"

# OAuth URLs
OAUTH_AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
OAUTH_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"

# API Base URLs
API_BASE_SANDBOX = "https://sandbox-quickbooks.api.intuit.com"
API_BASE_PRODUCTION = "https://quickbooks.api.intuit.com"

# Scopes
SCOPES = "com.intuit.quickbooks.accounting"


def get_api_base_url() -> str:
    """Get the correct API base URL based on environment"""
    if QBO_ENVIRONMENT == "sandbox":
        return API_BASE_SANDBOX
    return API_BASE_PRODUCTION


def get_auth_header() -> str:
    """Generate Basic Auth header for token requests"""
    credentials = f"{QBO_CLIENT_ID}:{QBO_CLIENT_SECRET}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return f"Basic {encoded}"


# ====== OAUTH FLOW ======

def get_authorization_url(state: Optional[str] = None) -> str:
    """
    Generate the OAuth2 authorization URL.
    User should be redirected to this URL to authorize the app.
    """
    params = {
        "client_id": QBO_CLIENT_ID,
        "redirect_uri": QBO_REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "state": state or "default"
    }

    query_string = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{OAUTH_AUTH_URL}?{query_string}"


async def exchange_code_for_tokens(code: str, realm_id: str) -> Dict[str, Any]:
    """
    Exchange authorization code for access and refresh tokens.
    Called after user authorizes and is redirected back.
    """
    async with httpx.AsyncClient() as client:
        response = await client.post(
            OAUTH_TOKEN_URL,
            headers={
                "Authorization": get_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json"
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": QBO_REDIRECT_URI
            }
        )

        if response.status_code != 200:
            raise Exception(f"Token exchange failed: {response.status_code} - {response.text}")

        token_data = response.json()

        # Calculate expiration times
        now = datetime.utcnow()
        access_expires = now + timedelta(seconds=token_data.get("expires_in", 3600))
        refresh_expires = now + timedelta(seconds=token_data.get("x_refresh_token_expires_in", 8726400))

        # Get company info
        company_name = await get_company_name(
            token_data["access_token"],
            realm_id
        )

        # Store tokens in database
        token_record = {
            "realm_id": realm_id,
            "company_name": company_name,
            "access_token": token_data["access_token"],
            "refresh_token": token_data["refresh_token"],
            "access_token_expires_at": access_expires.isoformat(),
            "refresh_token_expires_at": refresh_expires.isoformat(),
            "token_type": token_data.get("token_type", "Bearer"),
            "updated_at": now.isoformat()
        }

        # Upsert (update if exists, insert if not)
        supabase.table("qbo_tokens").upsert(
            token_record,
            on_conflict="realm_id"
        ).execute()

        return {
            "realm_id": realm_id,
            "company_name": company_name,
            "access_token_expires_at": access_expires.isoformat(),
            "refresh_token_expires_at": refresh_expires.isoformat()
        }


async def refresh_access_token(realm_id: str) -> Dict[str, Any]:
    """
    Refresh the access token using the refresh token.
    Called automatically when access token is expired.
    """
    # Get current tokens
    result = supabase.table("qbo_tokens") \
        .select("*") \
        .eq("realm_id", realm_id) \
        .single() \
        .execute()

    if not result.data:
        raise Exception(f"No tokens found for realm_id: {realm_id}")

    current_tokens = result.data

    async with httpx.AsyncClient() as client:
        response = await client.post(
            OAUTH_TOKEN_URL,
            headers={
                "Authorization": get_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json"
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": current_tokens["refresh_token"]
            }
        )

        if response.status_code != 200:
            raise Exception(f"Token refresh failed: {response.status_code} - {response.text}")

        token_data = response.json()

        # Calculate new expiration times
        now = datetime.utcnow()
        access_expires = now + timedelta(seconds=token_data.get("expires_in", 3600))
        refresh_expires = now + timedelta(seconds=token_data.get("x_refresh_token_expires_in", 8726400))

        # Update tokens in database
        supabase.table("qbo_tokens").update({
            "access_token": token_data["access_token"],
            "refresh_token": token_data["refresh_token"],
            "access_token_expires_at": access_expires.isoformat(),
            "refresh_token_expires_at": refresh_expires.isoformat(),
            "updated_at": now.isoformat()
        }).eq("realm_id", realm_id).execute()

        return {
            "realm_id": realm_id,
            "access_token_expires_at": access_expires.isoformat()
        }


async def get_valid_access_token(realm_id: str) -> str:
    """
    Get a valid access token, refreshing if necessary.
    """
    result = supabase.table("qbo_tokens") \
        .select("*") \
        .eq("realm_id", realm_id) \
        .single() \
        .execute()

    if not result.data:
        raise Exception(f"No tokens found for realm_id: {realm_id}. Please authorize first.")

    tokens = result.data

    # Check if access token is expired (with 5 min buffer)
    expires_str = tokens["access_token_expires_at"].replace("Z", "+00:00")
    expires_at = datetime.fromisoformat(expires_str)
    # Make naive for comparison with utcnow()
    if expires_at.tzinfo is not None:
        expires_at = expires_at.replace(tzinfo=None)
    if datetime.utcnow() >= (expires_at - timedelta(minutes=5)):
        # Token expired or about to expire, refresh it
        await refresh_access_token(realm_id)

        # Get updated tokens
        result = supabase.table("qbo_tokens") \
            .select("access_token") \
            .eq("realm_id", realm_id) \
            .single() \
            .execute()

        return result.data["access_token"]

    return tokens["access_token"]


# ====== QBO API CALLS ======

async def qbo_api_request(
    realm_id: str,
    endpoint: str,
    method: str = "GET",
    params: Optional[Dict] = None,
    json_data: Optional[Dict] = None
) -> Dict[str, Any]:
    """
    Make an authenticated request to the QBO API.
    Automatically handles token refresh.
    """
    access_token = await get_valid_access_token(realm_id)
    base_url = get_api_base_url()
    url = f"{base_url}/v3/company/{realm_id}/{endpoint}"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        if method == "GET":
            response = await client.get(url, headers=headers, params=params)
        elif method == "POST":
            response = await client.post(url, headers=headers, json=json_data)
        else:
            raise ValueError(f"Unsupported method: {method}")

        if response.status_code == 401:
            # Token might be invalid, try refreshing
            await refresh_access_token(realm_id)
            access_token = await get_valid_access_token(realm_id)
            headers["Authorization"] = f"Bearer {access_token}"

            if method == "GET":
                response = await client.get(url, headers=headers, params=params)
            else:
                response = await client.post(url, headers=headers, json=json_data)

        if response.status_code != 200:
            raise Exception(f"QBO API error: {response.status_code} - {response.text}")

        return response.json()


async def qbo_query(realm_id: str, query: str) -> Dict[str, Any]:
    """
    Execute a QBO query (SQL-like syntax).
    """
    return await qbo_api_request(
        realm_id,
        "query",
        params={"query": query}
    )


async def get_company_name(access_token: str, realm_id: str) -> str:
    """Get company name from QBO"""
    try:
        base_url = get_api_base_url()
        url = f"{base_url}/v3/company/{realm_id}/companyinfo/{realm_id}"

        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json"
                }
            )

            if response.status_code == 200:
                data = response.json()
                return data.get("CompanyInfo", {}).get("CompanyName", "Unknown Company")
    except Exception as _exc:
        logger.debug("Suppressed company name fetch: %s", _exc)

    return "Unknown Company"


# ====== EXPENSE FETCHING ======

async def fetch_all_purchases(
    realm_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Fetch all Purchase transactions from QBO.
    """
    query = "SELECT * FROM Purchase"
    if start_date:
        query += f" WHERE TxnDate >= '{start_date}'"
        if end_date:
            query += f" AND TxnDate <= '{end_date}'"
    elif end_date:
        query += f" WHERE TxnDate <= '{end_date}'"

    query += " MAXRESULTS 1000"

    result = await qbo_query(realm_id, query)
    return result.get("QueryResponse", {}).get("Purchase", [])


async def fetch_all_bills(
    realm_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Fetch all Bill transactions from QBO.
    """
    query = "SELECT * FROM Bill"
    if start_date:
        query += f" WHERE TxnDate >= '{start_date}'"
        if end_date:
            query += f" AND TxnDate <= '{end_date}'"
    elif end_date:
        query += f" WHERE TxnDate <= '{end_date}'"

    query += " MAXRESULTS 1000"

    result = await qbo_query(realm_id, query)
    return result.get("QueryResponse", {}).get("Bill", [])


async def fetch_all_vendor_credits(
    realm_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Fetch all VendorCredit transactions from QBO.
    """
    query = "SELECT * FROM VendorCredit"
    if start_date:
        query += f" WHERE TxnDate >= '{start_date}'"
        if end_date:
            query += f" AND TxnDate <= '{end_date}'"
    elif end_date:
        query += f" WHERE TxnDate <= '{end_date}'"

    query += " MAXRESULTS 1000"

    result = await qbo_query(realm_id, query)
    return result.get("QueryResponse", {}).get("VendorCredit", [])


async def fetch_all_journal_entries(
    realm_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Fetch all JournalEntry transactions from QBO.
    """
    query = "SELECT * FROM JournalEntry"
    if start_date:
        query += f" WHERE TxnDate >= '{start_date}'"
        if end_date:
            query += f" AND TxnDate <= '{end_date}'"
    elif end_date:
        query += f" WHERE TxnDate <= '{end_date}'"

    query += " MAXRESULTS 1000"

    result = await qbo_query(realm_id, query)
    return result.get("QueryResponse", {}).get("JournalEntry", [])


async def fetch_all_invoices(
    realm_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Fetch all Invoice transactions from QBO (Revenue/Income).
    """
    query = "SELECT * FROM Invoice"
    if start_date:
        query += f" WHERE TxnDate >= '{start_date}'"
        if end_date:
            query += f" AND TxnDate <= '{end_date}'"
    elif end_date:
        query += f" WHERE TxnDate <= '{end_date}'"

    query += " MAXRESULTS 1000"

    result = await qbo_query(realm_id, query)
    return result.get("QueryResponse", {}).get("Invoice", [])


async def fetch_all_sales_receipts(
    realm_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Fetch all SalesReceipt transactions from QBO (Revenue/Income).
    """
    query = "SELECT * FROM SalesReceipt"
    if start_date:
        query += f" WHERE TxnDate >= '{start_date}'"
        if end_date:
            query += f" AND TxnDate <= '{end_date}'"
    elif end_date:
        query += f" WHERE TxnDate <= '{end_date}'"

    query += " MAXRESULTS 1000"

    result = await qbo_query(realm_id, query)
    return result.get("QueryResponse", {}).get("SalesReceipt", [])


async def fetch_project_catalog(realm_id: str) -> Dict[str, str]:
    """
    Fetch all Jobs/Projects from QBO (Customers where Job=true).
    Returns dict: {customer_id: customer_name}
    """
    query = "SELECT Id, DisplayName, FullyQualifiedName FROM Customer WHERE Job = true AND Active = true MAXRESULTS 1000"

    result = await qbo_query(realm_id, query)
    customers = result.get("QueryResponse", {}).get("Customer", [])

    project_map = {}
    for c in customers:
        cid = str(c.get("Id", ""))
        name = c.get("FullyQualifiedName") or c.get("DisplayName") or f"Project_{cid}"
        project_map[cid] = name

    return project_map


async def fetch_accounts_metadata(realm_id: str) -> Dict[str, Dict]:
    """
    Fetch all accounts from QBO with their metadata.
    Returns dict: {account_id: {Name, AccountType, AccountSubType}}
    """
    query = "SELECT Id, Name, AccountType, AccountSubType FROM Account MAXRESULTS 1000"

    result = await qbo_query(realm_id, query)
    accounts = result.get("QueryResponse", {}).get("Account", [])

    account_map = {}
    for a in accounts:
        aid = str(a.get("Id", ""))
        account_map[aid] = {
            "Name": a.get("Name", ""),
            "AccountType": a.get("AccountType", ""),
            "AccountSubType": a.get("AccountSubType", "")
        }

    return account_map


# ====== CONNECTION STATUS ======

def get_connection_status() -> Dict[str, Any]:
    """
    Get the current QBO connection status.
    """
    try:
        result = supabase.table("qbo_tokens").select("*").execute()

        # Debug: log what we got
        logger.debug("[QBO] get_connection_status - result.data: %s", result.data)

        connections = []
        for token in (result.data or []):
            try:
                # Safe parsing of timestamps
                access_expires_str = token.get("access_token_expires_at")
                refresh_expires_str = token.get("refresh_token_expires_at")

                now = datetime.utcnow()
                access_valid = False
                refresh_valid = False

                if access_expires_str:
                    access_expires = datetime.fromisoformat(access_expires_str.replace("Z", "+00:00"))
                    if access_expires.tzinfo is not None:
                        access_expires = access_expires.replace(tzinfo=None)
                    access_valid = now < access_expires

                if refresh_expires_str:
                    refresh_expires = datetime.fromisoformat(refresh_expires_str.replace("Z", "+00:00"))
                    if refresh_expires.tzinfo is not None:
                        refresh_expires = refresh_expires.replace(tzinfo=None)
                    refresh_valid = now < refresh_expires

                connections.append({
                    "realm_id": token.get("realm_id"),
                    "company_name": token.get("company_name", "Unknown"),
                    "access_token_valid": access_valid,
                    "access_token_expires_at": access_expires_str,
                    "refresh_token_valid": refresh_valid,
                    "refresh_token_expires_at": refresh_expires_str,
                    "last_updated": token.get("updated_at")
                })
            except Exception as token_error:
                logger.warning("[QBO] Error processing token: %s", token_error)
                # Include the token with error info
                connections.append({
                    "realm_id": token.get("realm_id"),
                    "company_name": token.get("company_name", "Unknown"),
                    "access_token_valid": False,
                    "access_token_expires_at": None,
                    "refresh_token_valid": False,
                    "refresh_token_expires_at": None,
                    "last_updated": token.get("updated_at"),
                    "error": str(token_error)
                })

        return {
            "connected": len(connections) > 0,
            "connections": connections,
            "environment": QBO_ENVIRONMENT
        }
    except Exception as e:
        logger.error("[QBO] Error in get_connection_status: %s", e)
        # Return empty status instead of crashing
        return {
            "connected": False,
            "connections": [],
            "environment": QBO_ENVIRONMENT,
            "error": str(e)
        }


def disconnect(realm_id: str) -> bool:
    """
    Remove QBO connection (delete tokens).
    """
    supabase.table("qbo_tokens").delete().eq("realm_id", realm_id).execute()
    return True


# ====== BUDGET FETCHING ======

async def fetch_budgets(
    realm_id: str,
    fiscal_year: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Fetch all Budget records from QBO.
    QuickBooks budgets are divided into 12 monthly periods.
    """
    query = "SELECT * FROM Budget"
    if fiscal_year:
        query += f" WHERE FiscalYear = '{fiscal_year}'"

    query += " MAXRESULTS 1000"

    result = await qbo_query(realm_id, query)
    return result.get("QueryResponse", {}).get("Budget", [])
