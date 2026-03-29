#!/usr/bin/env python3
"""
Lotlytics MCP Server — Single unified server (free + premium)

Free tier:   No API key needed. Top 50 markets, 3 tools, watermarked.
Premium:     Pass X-API-Key (or Authorization: Bearer) header.
             Unlocks all 895 markets + compare + search tools.

Run:
  python server.py                                    # stdio (free/premium auto-detected per request)
  python server.py --transport sse --port 8080        # SSE server
"""

import argparse
import asyncio
import contextvars
import os
import re
import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LOTLYTICS_API = "https://api.lotlytics.us"

# Fallback env key (used when no per-request key is present)
_ENV_API_KEY = os.environ.get("LOTLYTICS_API_KEY") or os.environ.get("LOTLYTICS_INTERNAL_KEY")

# ---------------------------------------------------------------------------
# Per-request API key (set by middleware on each SSE/HTTP request)
# ---------------------------------------------------------------------------

_api_key_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("api_key", default=None)


def get_request_api_key() -> str | None:
    """Return the API key for the current request (header > env fallback)."""
    return _api_key_var.get() or _ENV_API_KEY


def is_premium() -> bool:
    """True if a valid-looking API key is available for this request."""
    key = get_request_api_key()
    return bool(key and len(key) > 10)


def api_headers() -> dict:
    """Return Authorization headers for Lotlytics API calls."""
    key = get_request_api_key()
    if key:
        return {"Authorization": f"Bearer {key}"}
    return {}


# ---------------------------------------------------------------------------
# Middleware — injects X-API-Key / Bearer token into ContextVar
# ---------------------------------------------------------------------------

class ApiKeyMiddleware:
    """ASGI middleware that extracts X-API-Key (or Bearer token) per request."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = {k.lower(): v for k, v in scope.get("headers", [])}
            api_key = headers.get(b"x-api-key", b"").decode("utf-8", errors="ignore").strip()
            if not api_key:
                auth = headers.get(b"authorization", b"").decode("utf-8", errors="ignore")
                if auth.lower().startswith("bearer "):
                    api_key = auth[7:].strip()
            token = _api_key_var.set(api_key or None)
            try:
                await self.app(scope, receive, send)
            finally:
                _api_key_var.reset(token)
        else:
            await self.app(scope, receive, send)


# ---------------------------------------------------------------------------
# Free tier market list
# ---------------------------------------------------------------------------

FREE_MARKETS = {
    "new-york-ny", "los-angeles-ca", "chicago-il", "dallas-tx", "houston-tx",
    "washington-dc", "miami-fl", "philadelphia-pa", "atlanta-ga", "phoenix-az",
    "boston-ma", "riverside-ca", "seattle-wa", "minneapolis-mn", "san-diego-ca",
    "tampa-fl", "denver-co", "st-louis-mo", "baltimore-md", "orlando-fl",
    "portland-or", "san-antonio-tx", "sacramento-ca", "pittsburgh-pa", "austin-tx",
    "las-vegas-nv", "cincinnati-oh", "kansas-city-mo", "columbus-oh", "indianapolis-in",
    "san-jose-ca", "cleveland-oh", "nashville-tn", "virginia-beach-va", "hartford-ct",
    "raleigh-nc", "salt-lake-city-ut", "richmond-va", "memphis-tn", "jacksonville-fl",
    "oklahoma-city-ok", "new-orleans-la", "louisville-ky", "charlotte-nc", "buffalo-ny",
    "birmingham-al", "providence-ri", "milwaukee-wi", "tucson-az", "fresno-ca",
}

# ---------------------------------------------------------------------------
# CTAs / watermarks
# ---------------------------------------------------------------------------

def upgrade_cta(city_name: str, signal: str = None) -> str:
    if signal:
        return (
            f"\n\n💡 *{city_name} is showing {signal} — see which ZIP codes are bucking the trend.*  \n"
            f"[Unlock full analysis with Investor plan →](https://lotlytics.us/pricing)"
        )
    return (
        f"\n\n💡 *Want ZIP-level data, HUD rents, and migration trends for {city_name}?*  \n"
        f"[Investor plan unlocks everything →](https://lotlytics.us/pricing)"
    )

PREMIUM_WATERMARK = "\n\n*Powered by [Lotlytics](https://lotlytics.us) · Investor Plan*"

# ---------------------------------------------------------------------------
# State / city normalization
# ---------------------------------------------------------------------------

STATE_ABBREVS = {
    "alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar",
    "california": "ca", "colorado": "co", "connecticut": "ct", "delaware": "de",
    "florida": "fl", "georgia": "ga", "hawaii": "hi", "idaho": "id",
    "illinois": "il", "indiana": "in", "iowa": "ia", "kansas": "ks",
    "kentucky": "ky", "louisiana": "la", "maine": "me", "maryland": "md",
    "massachusetts": "ma", "michigan": "mi", "minnesota": "mn", "mississippi": "ms",
    "missouri": "mo", "montana": "mt", "nebraska": "ne", "nevada": "nv",
    "new hampshire": "nh", "new jersey": "nj", "new mexico": "nm", "new york": "ny",
    "north carolina": "nc", "north dakota": "nd", "ohio": "oh", "oklahoma": "ok",
    "oregon": "or", "pennsylvania": "pa", "rhode island": "ri", "south carolina": "sc",
    "south dakota": "sd", "tennessee": "tn", "texas": "tx", "utah": "ut",
    "vermont": "vt", "virginia": "va", "washington": "wa", "west virginia": "wv",
    "wisconsin": "wi", "wyoming": "wy", "district of columbia": "dc",
}

CITY_ALIASES = {
    "new york city": "new-york", "nyc": "new-york", "la": "los-angeles",
    "sf": "san-francisco", "dc": "washington", "washington dc": "washington",
    "philly": "philadelphia", "vegas": "las-vegas", "nashvegas": "nashville",
}

DEFAULT_TIMEOUT = 15
FETCH_TIMEOUT = 60


def normalize_state(state: str) -> str:
    s = state.strip().lower()[:50]
    abbr = s if len(s) == 2 else STATE_ABBREVS.get(s, s[:2])
    if not re.fullmatch(r"[a-z]{2}", abbr):
        return "xx"
    return abbr


def normalize_city_input(city: str, state: str) -> str:
    city = city.strip()[:100]
    state = state.strip()[:50]
    if not city or not state:
        return "unknown-xx"
    state_abbr = normalize_state(state)
    city_lower = CITY_ALIASES.get(city.lower(), city.lower())
    city_slug = re.sub(r"[^a-z0-9\s-]", "", city_lower)
    city_slug = re.sub(r"\s+", "-", city_slug).strip("-")
    if not city_slug:
        return "unknown-xx"
    return f"{city_slug}-{state_abbr}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

INVALID_API_KEY_SENTINEL = "__invalid_api_key__"

async def fetch_market(region_id: str, use_key: bool = True) -> dict | None | str:
    headers = api_headers() if use_key else {}
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client:
        try:
            r = await client.get(
                f"{LOTLYTICS_API}/public/v1/markets/{region_id}/summary",
                headers=headers
            )
            if r.status_code == 200:
                try:
                    return r.json()
                except Exception:
                    return None
            if r.status_code in (401, 403):
                return INVALID_API_KEY_SENTINEL
        except httpx.HTTPError:
            pass
    return None


async def search_similar_markets(partial: str, state_abbr: str) -> list[str]:
    safe_partial = re.sub(r"[^a-z0-9]", "", partial.lower())[:4]
    if not safe_partial:
        return []
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            r = await client.get(f"{LOTLYTICS_API}/public/v1/regions", headers=api_headers())
            if r.status_code != 200:
                return []
            all_markets = r.json()
            return [
                m["regionId"] for m in all_markets
                if state_abbr in m["regionId"] and (
                    safe_partial in m.get("name", "").lower() or safe_partial in m["regionId"]
                )
            ][:5]
    except httpx.HTTPError:
        return []


def format_currency(val: float | None) -> str:
    if val is None:
        return "N/A"
    if val >= 1_000_000:
        return f"${val/1_000_000:.2f}M"
    if val >= 1_000:
        return f"${val:,.0f}"
    return f"${val:.2f}"


def format_pct(val: float | None) -> str:
    if val is None:
        return "N/A"
    try:
        v = float(val)
        sign = "+" if v > 0 else ""
        return f"{sign}{v:.1f}%"
    except (TypeError, ValueError):
        return "N/A"


def winner(val_a, val_b, higher_is_better=True):
    if val_a is None or val_b is None:
        return "—", "—"
    if higher_is_better:
        return ("✅", "  ") if val_a > val_b else ("  ", "✅")
    else:
        return ("✅", "  ") if val_a < val_b else ("  ", "✅")


def interpret_market(metrics: dict) -> str:
    lines = []
    appr = metrics.get("appreciation")
    momentum = metrics.get("marketMomentum", {}) or {}
    yield_ = metrics.get("rentalYield")
    p2i = metrics.get("priceToIncome")
    migration = metrics.get("incomeMigration", {}) or {}

    if appr is not None:
        if appr > 5:
            lines.append(f"Strong appreciation ({format_pct(appr)} YoY) — prices rising fast.")
        elif appr > 0:
            lines.append(f"Modest appreciation ({format_pct(appr)} YoY) — stable growth.")
        else:
            lines.append(f"Price correction ({format_pct(appr)} YoY) — market softening.")

    if momentum:
        lines.append(f"Market momentum: {momentum.get('label', 'N/A')} {momentum.get('emoji', '')}")
        months = momentum.get("monthsSupply")
        if months:
            if months < 3:
                lines.append(f"Inventory tight at {months:.1f} months supply (seller's market).")
            elif months > 6:
                lines.append(f"High inventory at {months:.1f} months supply (buyer's market).")

    if yield_ is not None:
        if yield_ > 6:
            lines.append(f"Strong rental yield ({yield_:.1f}%) — solid cash flow potential.")
        elif yield_ > 4:
            lines.append(f"Decent rental yield ({yield_:.1f}%).")
        else:
            lines.append(f"Low rental yield ({yield_:.1f}%) — difficult to cash flow.")

    if p2i is not None:
        if p2i > 6:
            lines.append(f"Expensive relative to local incomes (price-to-income: {p2i:.1f}x).")
        elif p2i < 3:
            lines.append(f"Affordable relative to local incomes (price-to-income: {p2i:.1f}x).")

    if migration:
        ratio_label = migration.get("ratioLabel", "")
        net_returns = migration.get("netReturns", 0) or 0
        if ratio_label:
            suffix = " (net population gain)" if net_returns > 0 else " (net population loss)" if net_returns < 0 else ""
            lines.append(f"Migration: {ratio_label}{suffix}.")

    bullish = sum([
        (appr or 0) > 2,
        (yield_ or 0) > 5,
        (p2i or 10) < 5,
        momentum.get("label") in ("Hot", "Warming"),
    ])
    bearish = sum([
        (appr or 0) < 0,
        (yield_ or 0) < 3,
        (p2i or 0) > 7,
        momentum.get("label") in ("Cooling", "Cold"),
    ])
    if bullish >= 3:
        lines.append("**Overall: Multiple bullish signals — favorable investment conditions.**")
    elif bearish >= 3:
        lines.append("**Overall: Multiple headwinds — approach with caution.**")
    elif bullish > bearish:
        lines.append("**Overall: More positives than negatives.**")

    return " ".join(lines) if lines else "Insufficient data for narrative interpretation."


# ---------------------------------------------------------------------------
# Single MCP server
# ---------------------------------------------------------------------------

_HOST = os.environ.get("HOST", "0.0.0.0")
_PORT = int(os.environ.get("PORT", 8080))

mcp = FastMCP(
    "Lotlytics",
    host=_HOST,
    port=_PORT,
    instructions="""
You have access to Lotlytics real estate market data for 895 US cities and metros.
Free tier covers the top 50 markets — no API key needed.
With an X-API-Key header (Investor plan), all 895 markets and advanced tools are unlocked.
Start with get_market_health for quick screening, then get_market_summary for full details.
Use compare_markets or search_markets for deeper investment research (requires API key).
""".strip()
)


# ---------------------------------------------------------------------------
# Tool: get_market_summary
# ---------------------------------------------------------------------------

@mcp.tool(
    description="""Get a real estate market summary for a US city or metro area.
Returns median home price, appreciation, rental yield, market momentum, and migration trends.
Free tier: top 50 markets. With API key: all 895 US metros.
Use get_market_health first for a quick score, then this for the full picture.
"""
)
async def get_market_summary(city: str, state: str) -> str:
    region_id = normalize_city_input(city, state)
    premium = is_premium()

    # Free tier market gate
    if not premium and region_id not in FREE_MARKETS:
        state_abbr = normalize_state(state)
        city_safe = re.sub(r"[^a-z0-9]", "", city.strip().lower())[:4]
        similar = [m for m in await search_similar_markets(city_safe, state_abbr) if m in FREE_MARKETS]
        msg = f"**{city}, {state}** is not available on the free tier (top 50 markets only)."
        if similar:
            msg += f" Try one of these nearby: {', '.join(similar)}"
        msg += (
            f"\n\n💡 *Unlock all 895 markets including {city} — add your API key from [lotlytics.us/settings/api-keys](https://lotlytics.us/settings/api-keys).*"
        )
        return msg

    data = await fetch_market(region_id)

    # Bad key but free market — retry without auth, serve free tier response
    if data == INVALID_API_KEY_SENTINEL and region_id in FREE_MARKETS:
        data = await fetch_market(region_id, use_key=False)
        premium = False  # force free tier response format

    if data == INVALID_API_KEY_SENTINEL:
        return (
            "**Invalid API key.** Your `X-API-Key` was not accepted.\n\n"
            "Check your key at [lotlytics.us/settings/api-keys](https://lotlytics.us/settings/api-keys) "
            "and make sure you're on the Investor plan."
        )

    if not data:
        state_abbr = normalize_state(state)
        city_safe = re.sub(r"[^a-z0-9]", "", city.strip().lower())[:4]
        similar = await search_similar_markets(city_safe, state_abbr)
        msg = f"Market not found for '{city}, {state}'."
        if similar:
            msg += f" Did you mean: {', '.join(similar)}?"
        else:
            msg += f" Try list_markets with state='{state}' to see available markets."
        return msg

    m = data.get("metrics", {})
    name = m.get("name", f"{city}, {state}")
    momentum = m.get("marketMomentum", {}) or {}
    short_name = name.split(",")[0].split("-")[0].strip()

    if premium:
        # Full premium report
        pct = m.get("percentiles", {}) or {}
        migration = m.get("incomeMigration", {}) or {}
        hud = m.get("hudFmr", {}) or {}
        net_returns = migration.get("netReturns", "N/A")
        net_returns_fmt = f"{net_returns:,}" if isinstance(net_returns, (int, float)) else "N/A"

        return f"""## {name} — Full Market Report

**Prices & Appreciation**
- Median home price: {format_currency(m.get('medianPrice'))} (top {100 - pct.get('medianPrice', 50)}% of US markets)
- YoY appreciation: {format_pct(m.get('appreciation'))} ({pct.get('appreciation', 'N/A')}th percentile)
- Price-to-income ratio: {m.get('priceToIncome', 'N/A')}x ({pct.get('priceToIncome', 'N/A')}th percentile)
- Affordability score: {m.get('affordability', 'N/A')}/100

**Rentals & Yield**
- Median rent: {format_currency(m.get('latestRent'))}/mo
- Rental yield: {m.get('rentalYield', 'N/A')}% ({pct.get('rentalYield', 'N/A')}th percentile)
- Estimated mortgage (20% down): {format_currency(m.get('mortgagePayment'))}/mo

**HUD Fair Market Rents (FY{hud.get('fiscalYear', 'N/A')})**
- Studio: {format_currency(hud.get('fmr0br'))}/mo | 1BR: {format_currency(hud.get('fmr1br'))}/mo | 2BR: {format_currency(hud.get('fmr2br'))}/mo
- 3BR: {format_currency(hud.get('fmr3br'))}/mo | 4BR: {format_currency(hud.get('fmr4br'))}/mo

**Market Conditions**
- Momentum: {momentum.get('label', 'N/A')} {momentum.get('emoji', '')}
- Months of supply: {momentum.get('monthsSupply', 'N/A')}
- Sale-to-list ratio: {momentum.get('saleToList', 'N/A')}
- Price drop %: {momentum.get('priceDropPct', 'N/A')}%

**People & Economy**
- Median household income: {format_currency(m.get('medianIncome'))}
- Migration pattern: {migration.get('ratioLabel', 'N/A')}
- Net migration (tax filers): {net_returns_fmt}
- Avg incoming AGI: {format_currency(migration.get('avgIncomingAgi'))}

**Percentile Rankings (vs ~895 US markets)**
- Appreciation: {pct.get('appreciation', 'N/A')}th | Rental yield: {pct.get('rentalYield', 'N/A')}th
- Net migration: {pct.get('netMigration', 'N/A')}th | Employment: {pct.get('unemploymentRate', 'N/A')}th

**Analysis**
{interpret_market(m)}
{PREMIUM_WATERMARK}"""

    else:
        # Free tier report
        appr = m.get("appreciation") or 0
        cta_signal = None
        if appr < -3:
            cta_signal = f"a -{abs(appr):.1f}% price correction"
        elif appr > 5:
            cta_signal = f"+{appr:.1f}% appreciation"

        return f"""## {name}

**Prices**
- Median home price: {format_currency(m.get('medianPrice'))}
- YoY appreciation: {format_pct(m.get('appreciation'))}
- Price-to-income ratio: {m.get('priceToIncome', 'N/A')}x

**Rentals**
- Median rent: {format_currency(m.get('latestRent'))}/mo
- Rental yield: {m.get('rentalYield', 'N/A')}%
- Estimated mortgage: {format_currency(m.get('mortgagePayment'))}/mo

**Market Conditions**
- Momentum: {momentum.get('label', 'N/A')} {momentum.get('emoji', '')}
- Months of supply: {momentum.get('monthsSupply', 'N/A')}

**Summary**
{interpret_market(m)}
{upgrade_cta(short_name, cta_signal)}"""


# ---------------------------------------------------------------------------
# Tool: list_markets
# ---------------------------------------------------------------------------

@mcp.tool(
    description="""List available real estate markets.
Free tier: top 50 US metros. With API key: all 895 markets.
Optionally filter by state (2-letter abbreviation or full name).
"""
)
async def list_markets(state: str = "") -> str:
    premium = is_premium()
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            r = await client.get(f"{LOTLYTICS_API}/public/v1/regions", headers=api_headers())
            if r.status_code != 200:
                return "Failed to fetch market list — please try again."
            markets = r.json()
    except httpx.HTTPError:
        return "Failed to fetch market list — network error."

    if state:
        state_abbr = normalize_state(state)
        markets = [m for m in markets if m["regionId"].endswith(f"-{state_abbr}")]

    if not markets:
        return f"No markets found for state '{state}'. Check spelling or try a 2-letter state code."

    if premium:
        lines = [f"**{len(markets)} markets available{' in ' + state.title() if state else ''}:**\n"]
        for m in markets[:50]:
            lines.append(f"- {m.get('name', m['regionId'])} (`{m['regionId']}`)")
        if len(markets) > 50:
            lines.append(f"\n…and {len(markets) - 50} more. Filter by state to see all.")
        lines.append(PREMIUM_WATERMARK)
        return "\n".join(lines)
    else:
        free = [m for m in markets if m["regionId"] in FREE_MARKETS]
        lines = [f"**{len(free)} free markets{' in ' + state.title() if state else ''} (of {len(markets)} total):**\n"]
        for m in free[:20]:
            lines.append(f"- {m.get('name', m['regionId'])} (`{m['regionId']}`)")
        if len(markets) > len(free):
            lines.append(
                f"\n💡 *{len(markets) - len(free)} more markets available with Investor plan.*  \n"
                f"[Unlock all {len(markets)} markets →](https://lotlytics.us/pricing)"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: get_market_health
# ---------------------------------------------------------------------------

@mcp.tool(
    description="""Get a quick investment health score (1-10) for a US real estate market.
Returns a score with label (Strong Buy / Favorable / Neutral / Caution / Avoid) and key signals.
Use this FIRST for quick screening before digging deeper with get_market_summary.
Free tier: top 50 markets. With API key: all 895 metros.
"""
)
async def get_market_health(city: str, state: str) -> str:
    region_id = normalize_city_input(city, state)
    premium = is_premium()

    if not premium and region_id not in FREE_MARKETS:
        return (
            f"**{city}, {state}** is not available on the free tier.\n\n"
            f"💡 *Add your API key from [lotlytics.us/settings/api-keys](https://lotlytics.us/settings/api-keys) to unlock all 895 markets.*"
        )

    data = await fetch_market(region_id)

    # Bad key but free market — retry without auth
    if data == INVALID_API_KEY_SENTINEL and region_id in FREE_MARKETS:
        data = await fetch_market(region_id, use_key=False)
        premium = False

    if data == INVALID_API_KEY_SENTINEL:
        return (
            "**Invalid API key.** Your `X-API-Key` was not accepted.\n\n"
            "Check your key at [lotlytics.us/settings/api-keys](https://lotlytics.us/settings/api-keys) "
            "and make sure you're on the Investor plan."
        )
    if not data:
        return f"Market not found for '{city}, {state}'. Try list_markets to find available cities."

    m = data.get("metrics", {})
    pct = m.get("percentiles", {}) or {}
    name = m.get("name", f"{city}, {state}")
    short_name = name.split(",")[0].split("-")[0].strip()
    momentum = m.get("marketMomentum", {}) or {}

    score_inputs = [
        pct.get("appreciation", 50),
        pct.get("rentalYield", 50),
        100 - pct.get("priceToIncome", 50),
        pct.get("netMigration", 50),
        100 - pct.get("unemploymentRate", 50),
    ]
    score = round(sum(score_inputs) / max(len(score_inputs), 1) / 10, 1)
    score = max(1.0, min(10.0, score))

    if score >= 7.5:
        label, emoji = "Strong Buy", "🟢"
    elif score >= 6.0:
        label, emoji = "Favorable", "🟡"
    elif score >= 4.5:
        label, emoji = "Neutral", "⚪"
    elif score >= 3.0:
        label, emoji = "Caution", "🟠"
    else:
        label, emoji = "Avoid", "🔴"

    result = f"""## {name} — Market Health

{emoji} **{label}** ({score}/10)

**Key signals:**
- Appreciation: {format_pct(m.get('appreciation'))} YoY ({pct.get('appreciation', 'N/A')}th percentile)
- Rental yield: {m.get('rentalYield', 'N/A')}% ({pct.get('rentalYield', 'N/A')}th percentile)
- Market momentum: {momentum.get('label', 'N/A')} {momentum.get('emoji', '')}
- Net migration: {pct.get('netMigration', 'N/A')}th percentile
- Employment: {pct.get('unemploymentRate', 'N/A')}th percentile

*Score based on 5 factors vs ~895 US markets. Not investment advice.*"""

    if premium:
        result += PREMIUM_WATERMARK
    else:
        result += (
            f"\n\n💡 *{short_name} is scoring {label.lower()} — see ZIP-level breakdown and migration details.*  \n"
            f"[Investor plan →](https://lotlytics.us/pricing)"
        )

    return result


# ---------------------------------------------------------------------------
# Tool: compare_markets (premium only)
# ---------------------------------------------------------------------------

@mcp.tool(
    description="""Compare two real estate markets side by side. Requires API key (Investor plan).
Returns a head-to-head comparison with winners highlighted across price, yield, appreciation, and more.
Use when deciding between two markets for investment.
"""
)
async def compare_markets(city_a: str, state_a: str, city_b: str, state_b: str) -> str:
    if not is_premium():
        return (
            "**compare_markets requires an Investor plan API key.**\n\n"
            "💡 *Add your X-API-Key header from [lotlytics.us/settings/api-keys](https://lotlytics.us/settings/api-keys) to unlock this tool.*"
        )

    id_a = normalize_city_input(city_a, state_a)
    id_b = normalize_city_input(city_b, state_b)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            results = await asyncio.gather(
                client.get(f"{LOTLYTICS_API}/public/v1/markets/{id_a}/summary", headers=api_headers()),
                client.get(f"{LOTLYTICS_API}/public/v1/markets/{id_b}/summary", headers=api_headers()),
                return_exceptions=True
            )
    except Exception as e:
        return f"Failed to fetch market data: {type(e).__name__}"

    r_a, r_b = results
    if isinstance(r_a, Exception) or (hasattr(r_a, "status_code") and r_a.status_code != 200):
        return f"Market not found: '{city_a}, {state_a}'"
    if isinstance(r_b, Exception) or (hasattr(r_b, "status_code") and r_b.status_code != 200):
        return f"Market not found: '{city_b}, {state_b}'"

    a = r_a.json().get("metrics", {})
    b = r_b.json().get("metrics", {})
    name_a = a.get("name", f"{city_a}, {state_a}")
    name_b = b.get("name", f"{city_b}, {state_b}")
    short_a = name_a.split(",")[0]
    short_b = name_b.split(",")[0]

    mp_w = winner(a.get("medianPrice"), b.get("medianPrice"), higher_is_better=False)
    ap_w = winner(a.get("appreciation"), b.get("appreciation"))
    ry_w = winner(a.get("rentalYield"), b.get("rentalYield"))
    p2i_w = winner(a.get("priceToIncome"), b.get("priceToIncome"), higher_is_better=False)

    return f"""## {short_a} vs {short_b}

| Metric | {short_a} | {short_b} |
|--------|{'-'*20}|{'-'*20}|
| Median Price | {mp_w[0]} {format_currency(a.get('medianPrice'))} | {mp_w[1]} {format_currency(b.get('medianPrice'))} |
| YoY Appreciation | {ap_w[0]} {format_pct(a.get('appreciation'))} | {ap_w[1]} {format_pct(b.get('appreciation'))} |
| Rental Yield | {ry_w[0]} {a.get('rentalYield', 'N/A')}% | {ry_w[1]} {b.get('rentalYield', 'N/A')}% |
| Median Rent | {format_currency(a.get('latestRent'))}/mo | {format_currency(b.get('latestRent'))}/mo |
| Price-to-Income | {p2i_w[0]} {a.get('priceToIncome', 'N/A')}x | {p2i_w[1]} {b.get('priceToIncome', 'N/A')}x |
| Median Income | {format_currency(a.get('medianIncome'))} | {format_currency(b.get('medianIncome'))} |
| Momentum | {(a.get('marketMomentum') or {}).get('label', 'N/A')} | {(b.get('marketMomentum') or {}).get('label', 'N/A')} |
| Migration | {(a.get('incomeMigration') or {}).get('ratioLabel', 'N/A')} | {(b.get('incomeMigration') or {}).get('ratioLabel', 'N/A')} |

**{short_a}:** {interpret_market(a)}

**{short_b}:** {interpret_market(b)}
{PREMIUM_WATERMARK}"""


# ---------------------------------------------------------------------------
# Tool: search_markets (premium only)
# ---------------------------------------------------------------------------

@mcp.tool(
    description="""Search for real estate markets matching specific investment criteria. Requires API key.
Examples: 'markets in Texas under $300k with 6%+ yield', 'best appreciation in the Southeast'.
Filters: state, max_price, min_appreciation, min_rental_yield, max_price_to_income.
No filters = top-ranked markets nationally.
"""
)
async def search_markets(
    state: str = "",
    max_price: float = 0,
    min_appreciation: float = -999,
    min_rental_yield: float = 0,
    max_price_to_income: float = 999,
    limit: int = 10
) -> str:
    if not is_premium():
        return (
            "**search_markets requires an Investor plan API key.**\n\n"
            "💡 *Add your X-API-Key header from [lotlytics.us/settings/api-keys](https://lotlytics.us/settings/api-keys) to unlock this tool.*"
        )

    limit = max(1, min(limit, 25))

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            r = await client.get(f"{LOTLYTICS_API}/public/v1/regions", headers=api_headers())
            if r.status_code != 200:
                return "Failed to fetch market list."
            all_markets = r.json()
    except httpx.HTTPError:
        return "Failed to fetch market list — network error."

    if state:
        state_abbr = normalize_state(state)
        all_markets = [m for m in all_markets if m["regionId"].endswith(f"-{state_abbr}")]

    total_available = len(all_markets)
    SAMPLE_SIZE = 20
    sample = all_markets[:SAMPLE_SIZE]

    semaphore = asyncio.Semaphore(3)

    async def fetch_one(region_id: str):
        async with semaphore:
            return await fetch_market(region_id)

    responses = await asyncio.gather(
        *[fetch_one(m["regionId"]) for m in sample],
        return_exceptions=True
    )

    results = []
    for i, data in enumerate(responses):
        if isinstance(data, Exception) or not data:
            continue
        m = data.get("metrics", {})
        price = m.get("medianPrice") or 0
        appr = m.get("appreciation") or -999
        yield_ = m.get("rentalYield") or 0
        p2i = m.get("priceToIncome") or 999

        if max_price and price > max_price:
            continue
        if appr < min_appreciation:
            continue
        if yield_ < min_rental_yield:
            continue
        if p2i > max_price_to_income:
            continue

        results.append({
            "name": m.get("name", sample[i]["regionId"]),
            "price": price, "appreciation": appr,
            "yield": yield_, "p2i": p2i,
            "momentum": (m.get("marketMomentum") or {}).get("label", "N/A")
        })

    if not results:
        return "No markets found matching your criteria. Try relaxing the filters."

    results.sort(key=lambda x: (x["yield"] * 0.4 + x["appreciation"] * 0.4 - x["p2i"] * 0.2), reverse=True)
    results = results[:limit]

    sampling_note = ""
    if total_available > SAMPLE_SIZE:
        sampling_note = f"\n⚠️ Sampled {SAMPLE_SIZE} of {total_available} available markets. Filter by state for more complete results.\n"

    lines = [f"**Top {len(results)} markets matching your criteria:**\n{sampling_note}"]
    for r in results:
        lines.append(
            f"**{r['name']}**\n"
            f"Price: {format_currency(r['price'])} | Appreciation: {format_pct(r['appreciation'])} | "
            f"Yield: {r['yield']}% | P/I: {r['p2i']}x | Momentum: {r['momentum']}\n"
        )

    return "\n".join(lines) + PREMIUM_WATERMARK


# ---------------------------------------------------------------------------
# Health check endpoint
# ---------------------------------------------------------------------------

def add_health_endpoint(mcp_server: FastMCP):
    from starlette.routing import Route
    from starlette.responses import JSONResponse

    original_sse_app = mcp_server.sse_app

    def patched_sse_app(mount_path=None):
        app = original_sse_app(mount_path)

        async def health(request):
            return JSONResponse({"status": "ok", "service": "lotlytics-mcp"})

        app.routes.insert(0, Route("/health", health))

        # Wrap with our API key middleware
        app.middleware_stack = None  # reset so middleware is rebuilt
        app.add_middleware(ApiKeyMiddleware)  # type: ignore

        return app

    mcp_server.sse_app = patched_sse_app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    port = int(os.environ.get("PORT", args.port))

    print(f"Starting Lotlytics MCP (transport={args.transport}, port={port})", flush=True)
    print(f"Mode: {'premium env key loaded' if _ENV_API_KEY else 'free (no env key)'}", flush=True)

    if args.transport in ("sse", "streamable-http"):
        add_health_endpoint(mcp)

    mcp.run(transport=args.transport)
