#!/usr/bin/env python3
"""
Lotlytics MCP Server
Exposes real estate market data to any MCP-compatible AI assistant.

Free tier:  No auth, IP rate limited, watermarked
Premium:    Requires LOTLYTICS_API_KEY env var (Investor+ plan)

Run:
  python server.py --mode free    # Free MCP (stdio)
  python server.py --mode premium # Premium MCP (stdio)
  python server.py --mode free --transport sse --port 8080  # HTTP server
"""

import argparse
import asyncio
import os
import re
import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LOTLYTICS_API = "https://api.lotlytics.us"
LOTLYTICS_API_KEY = os.environ.get("LOTLYTICS_API_KEY") or os.environ.get("LOTLYTICS_INTERNAL_KEY")

# Free tier: top 50 major US metros only
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

def upgrade_cta(city_name: str, signal: str = None) -> str:
    """Generate a contextual upgrade CTA based on what data we withheld."""
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
# Shared state abbreviation lookup (single source of truth)
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
    "new york city": "new-york",
    "nyc": "new-york",
    "la": "los-angeles",
    "sf": "san-francisco",
    "dc": "washington",
    "washington dc": "washington",
    "philly": "philadelphia",
    "vegas": "las-vegas",
    "nashvegas": "nashville",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT = 10   # seconds for list/search calls
FETCH_TIMEOUT = 30    # seconds for individual city fetches (Railway cold start)

def normalize_state(state: str) -> str:
    """Convert any state format to 2-letter abbreviation. Validates output."""
    s = state.strip().lower()[:50]
    abbr = s if len(s) == 2 else STATE_ABBREVS.get(s, s[:2])
    # Strict validation: must be exactly two lowercase ASCII letters
    if not re.fullmatch(r"[a-z]{2}", abbr):
        return "xx"  # safe sentinel — will produce a not-found result
    return abbr


def normalize_city_input(city: str, state: str) -> str:
    """Convert city/state to Lotlytics regionId slug."""
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


def api_headers() -> dict:
    """Return headers for Lotlytics API calls, including auth if available."""
    if LOTLYTICS_API_KEY:
        return {"Authorization": f"Bearer {LOTLYTICS_API_KEY}"}
    return {}


async def fetch_market(region_id: str) -> dict | None:
    """Fetch market summary from Lotlytics API."""
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client:
        try:
            r = await client.get(
                f"{LOTLYTICS_API}/api/v1/city/{region_id}/summary",
                headers=api_headers()
            )
            if r.status_code == 200:
                try:
                    return r.json()
                except Exception:
                    return None
        except httpx.HTTPError:
            pass
        return None


async def search_similar_markets(partial: str, state_abbr: str) -> list[str]:
    """Find similar market names for helpful error messages."""
    # Sanitize partial — use only alphanumeric chars
    safe_partial = re.sub(r"[^a-z0-9]", "", partial.lower())[:4]
    if not safe_partial:
        return []
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            r = await client.get(
                f"{LOTLYTICS_API}/api/v1/regions/list",
                headers=api_headers()
            )
            if r.status_code != 200:
                return []
            try:
                all_markets = r.json()
            except Exception:
                return []
            return [
                m["regionId"] for m in all_markets
                if state_abbr in m["regionId"] and (
                    safe_partial in m.get("name", "").lower() or
                    safe_partial in m["regionId"]
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
    """Return winner markers for comparison tables."""
    if val_a is None or val_b is None:
        return "—", "—"
    if higher_is_better:
        return ("✅", "  ") if val_a > val_b else ("  ", "✅")
    else:
        return ("✅", "  ") if val_a < val_b else ("  ", "✅")


def interpret_market(metrics: dict) -> str:
    """Generate AI-friendly narrative interpretation of a market."""
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

    # Migration: use the label as-is, add context only for positive patterns
    if migration:
        ratio_label = migration.get("ratioLabel", "")
        net_returns = migration.get("netReturns", 0) or 0
        if ratio_label:
            suffix = " (net population gain)" if net_returns > 0 else " (net population loss)" if net_returns < 0 else ""
            lines.append(f"Migration: {ratio_label}{suffix}.")

    # Net verdict
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
# MCP Server — Free Tier
# ---------------------------------------------------------------------------

_HOST = os.environ.get("HOST", "0.0.0.0")
_PORT = int(os.environ.get("PORT", 8080))
_MODE = os.environ.get("MCP_MODE", "free")  # Set via env or --mode arg

free_mcp = FastMCP(
    "Lotlytics Free",
    host=_HOST,
    port=_PORT,
    instructions="""
You have access to Lotlytics real estate market data for 895 US cities and metros.
Start with get_market_health for quick screening, then get_market_summary for the full picture.
Use list_markets to discover available cities or when a city search fails.
For ZIP-level data, rent analysis, HUD fair market rents, and market comparison, users need the Investor plan at lotlytics.us/pricing.
""".strip()
)


@free_mcp.tool(
    description="""Get a real estate market summary for a US city or metro area.
Returns median home price, year-over-year appreciation, rental yield, market momentum,
affordability score, and migration trends.
Use this when someone asks about a housing market, whether to invest in a city,
or wants to understand real estate conditions in a specific location.
Tip: use get_market_health first for a quick score, then this for the full picture.
Input: city name (e.g. 'Austin', 'New York City') and state (full name or 2-letter abbreviation).
"""
)
async def get_market_summary(city: str, state: str) -> str:
    region_id = normalize_city_input(city, state)

    # Free tier: top 50 markets only
    if region_id not in FREE_MARKETS:
        state_abbr = normalize_state(state)
        city_safe = re.sub(r"[^a-z0-9]", "", city.strip().lower())[:4]
        similar = [m for m in await search_similar_markets(city_safe, state_abbr) if m in FREE_MARKETS]
        msg = f"**{city}, {state}** is not available on the free tier (top 50 markets only)."
        if similar:
            msg += f" Try one of these free markets nearby: {', '.join(similar)}"
        msg += (
            f"\n\n💡 *Unlock all 895 markets including {city} with the Investor plan.*  \n"
            f"[See pricing →](https://lotlytics.us/pricing)"
        )
        return msg

    data = await fetch_market(region_id)

    if not data:
        state_abbr = normalize_state(state)
        city_safe = re.sub(r"[^a-z0-9]", "", city.strip().lower())[:4]
        similar = await search_similar_markets(city_safe, state_abbr)
        msg = f"Market not found for '{city}, {state}'."
        if similar:
            msg += f" Did you mean one of these? {', '.join(similar)}"
        else:
            msg += f" Try list_markets with state='{state}' to see available markets."
        return msg

    m = data.get("metrics", {})
    name = m.get("name", f"{city}, {state}")
    momentum = m.get("marketMomentum", {}) or {}
    short_name = name.split(",")[0].split("-")[0].strip()

    # Pick contextual CTA signal
    appr = m.get("appreciation") or 0
    if appr < -3:
        cta_signal = f"a -{abs(appr):.1f}% price correction"
    elif appr > 5:
        cta_signal = f"+{appr:.1f}% appreciation"
    else:
        cta_signal = None

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


@free_mcp.tool(
    description="""List available real estate markets in Lotlytics.
Use this when you need to know which cities/metros are available, or when a city search fails.
Optionally filter by state (2-letter abbreviation or full state name).
Calling with no filters returns all 895 available markets.
Returns market names and their IDs.
"""
)
async def list_markets(state: str = "") -> str:
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            r = await client.get(
                f"{LOTLYTICS_API}/api/v1/regions/list",
                headers=api_headers()
            )
            if r.status_code != 200:
                return "Failed to fetch market list — please try again."
            try:
                markets = r.json()
            except Exception:
                return "Failed to parse market list — please try again."
    except httpx.HTTPError:
        return "Failed to fetch market list — network error."

    if state:
        state_abbr = normalize_state(state)
        markets = [m for m in markets if m["regionId"].endswith(f"-{state_abbr}")]

    if not markets:
        return f"No markets found for state '{state}'. Check spelling or try a 2-letter state code."

    # Free tier: only show markets in FREE_MARKETS set
    free_markets_filtered = [m for m in markets if m["regionId"] in FREE_MARKETS]
    total_available = len(markets)
    total_free = len(free_markets_filtered)

    display = free_markets_filtered if free_markets_filtered else markets[:10]

    lines = [f"**{total_free} free markets available{' in ' + state.title() if state else ''}** (of {total_available} total):\n"]
    for m in display[:20]:
        lines.append(f"- {m.get('name', m['regionId'])} (`{m['regionId']}`)")

    if total_available > total_free:
        lines.append(
            f"\n💡 *{total_available - total_free} more markets available in {state.title() if state else 'the US'} with Investor plan.*  \n"
            f"[Unlock all {total_available} markets →](https://lotlytics.us/pricing)"
        )

    return "\n".join(lines)


@free_mcp.tool(
    description="""Get a quick investment health score (1-10) for a real estate market.
Returns a score with label (Strong Buy / Favorable / Neutral / Caution / Avoid) and key signals.
Use this FIRST for quick screening before deciding whether to dig deeper with get_market_summary.
Great for comparing multiple markets quickly or for yes/no investment questions.
Input: city name and state.
"""
)
async def get_market_health(city: str, state: str) -> str:
    region_id = normalize_city_input(city, state)
    data = await fetch_market(region_id)

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

    # Free tier: show score + top 2 signals only, hide the rest
    appr_pct = pct.get('appreciation', 50)
    migration_pct = pct.get('netMigration', 50)
    top_signal = "strong migration demand" if migration_pct >= 75 else "weak appreciation" if appr_pct < 25 else "mixed signals"

    return f"""## {name} — Market Health

{emoji} **{label}** ({score}/10)

**Top signals:**
- Appreciation: {format_pct(m.get('appreciation'))} YoY
- Rental yield: {m.get('rentalYield', 'N/A')}%
- Market momentum: {momentum.get('label', 'N/A')} {momentum.get('emoji', '')}

*Score based on 5 factors vs ~895 US markets. Not investment advice.*

💡 *{short_name} is scoring {label.lower()} — see the full breakdown including migration rank, employment percentile, and which ZIPs are outperforming.*  
[Investor plan →](https://lotlytics.us/pricing)"""


# ---------------------------------------------------------------------------
# MCP Server — Premium Tier
# ---------------------------------------------------------------------------

premium_mcp = FastMCP(
    "Lotlytics",
    host=_HOST,
    port=_PORT,
    instructions="""
You have full access to Lotlytics real estate market data for 895 US cities and metros.
Data sources: Zillow, HUD (Fair Market Rents), IRS (migration), Census, BLS.
Start with get_market_health for screening, get_market_summary_premium for full analysis,
compare_markets for head-to-head, or search_markets to find markets by criteria.
""".strip()
)


@premium_mcp.tool(
    description="""Get a comprehensive real estate market summary for a US city or metro.
Includes: prices, appreciation, rental yield, HUD fair market rents by bedroom count,
affordability, market momentum, migration trends, income data, and percentile rankings vs all US markets.
HUD Fair Market Rents are government-set rent benchmarks — particularly useful for Section 8 / voucher analysis.
Use for deep market analysis, investment decisions, or detailed market questions.
"""
)
async def get_market_summary_premium(city: str, state: str) -> str:
    region_id = normalize_city_input(city, state)
    data = await fetch_market(region_id)

    if not data:
        state_abbr = normalize_state(state)
        city_safe = re.sub(r"[^a-z0-9]", "", city.strip().lower())[:4]
        similar = await search_similar_markets(city_safe, state_abbr)
        msg = f"Market not found for '{city}, {state}'."
        if similar:
            msg += f" Similar markets: {', '.join(similar)}"
        return msg

    m = data.get("metrics", {})
    pct = m.get("percentiles", {}) or {}
    momentum = m.get("marketMomentum", {}) or {}
    migration = m.get("incomeMigration", {}) or {}
    hud = m.get("hudFmr", {}) or {}
    name = m.get("name", f"{city}, {state}")
    net_returns = migration.get('netReturns', 'N/A')
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
- Rent-to-price ratio: {m.get('rentToPrice', 'N/A')}
- Estimated mortgage (20% down): {format_currency(m.get('mortgagePayment'))}/mo

**HUD Fair Market Rents (FY{hud.get('fiscalYear', 'N/A')})**
- Studio: {format_currency(hud.get('fmr0br'))}/mo
- 1BR: {format_currency(hud.get('fmr1br'))}/mo
- 2BR: {format_currency(hud.get('fmr2br'))}/mo
- 3BR: {format_currency(hud.get('fmr3br'))}/mo
- 4BR: {format_currency(hud.get('fmr4br'))}/mo

**Market Conditions**
- Momentum: {momentum.get('label', 'N/A')} {momentum.get('emoji', '')}
- Months of supply: {momentum.get('monthsSupply', 'N/A')}
- Sale-to-list ratio: {momentum.get('saleToList', 'N/A')}
- Price drop %: {momentum.get('priceDropPct', 'N/A')}%
- DOM YoY change: {momentum.get('domYoyChange', 'N/A')} days

**People & Economy**
- Median household income: {format_currency(m.get('medianIncome'))}
- Migration pattern: {migration.get('ratioLabel', 'N/A')}
- Avg incoming AGI: {format_currency(migration.get('avgIncomingAgi'))}
- Avg outgoing AGI: {format_currency(migration.get('avgOutgoingAgi'))}
- Net migration (tax filers): {net_returns_fmt}

**Percentile Rankings (vs ~895 US markets)**
- Appreciation: {pct.get('appreciation', 'N/A')}th | Rental yield: {pct.get('rentalYield', 'N/A')}th
- Net migration: {pct.get('netMigration', 'N/A')}th | Employment: {pct.get('unemploymentRate', 'N/A')}th
- Climate risk: {pct.get('climateRisk', 'N/A')}th (lower = safer)

**Analysis**
{interpret_market(m)}
{PREMIUM_WATERMARK}"""


@premium_mcp.tool(
    description="""Compare two real estate markets side by side.
Returns a structured comparison table with winners highlighted.
Use when someone is deciding between two markets, or wants to understand
relative strengths of two locations.
For comparing 3+ markets at once, call this tool multiple times or use search_markets.
"""
)
async def compare_markets(city_a: str, state_a: str, city_b: str, state_b: str) -> str:
    id_a = normalize_city_input(city_a, state_a)
    id_b = normalize_city_input(city_b, state_b)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            results = await asyncio.gather(
                client.get(f"{LOTLYTICS_API}/api/v1/city/{id_a}/summary", headers=api_headers()),
                client.get(f"{LOTLYTICS_API}/api/v1/city/{id_b}/summary", headers=api_headers()),
                return_exceptions=True
            )
    except Exception as e:
        return f"Failed to fetch market data: {type(e).__name__}"

    r_a, r_b = results
    if isinstance(r_a, Exception) or (hasattr(r_a, 'status_code') and r_a.status_code != 200):
        return f"Market not found or unreachable: '{city_a}, {state_a}'"
    if isinstance(r_b, Exception) or (hasattr(r_b, 'status_code') and r_b.status_code != 200):
        return f"Market not found or unreachable: '{city_b}, {state_b}'"

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


@premium_mcp.tool(
    description="""Search for real estate markets that match specific investment criteria.
Use when someone wants to find markets based on conditions like:
- 'Show me markets with rental yield above 6%'
- 'Find affordable markets in Texas under $300k'
- 'Best markets in the Southeast for appreciation'
Calling with no filters returns the top-ranked markets nationally.
Filters: state, max_price, min_appreciation, min_rental_yield, max_price_to_income.
Returns top matching markets ranked by composite score (yield + appreciation - overvaluation).
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
    limit = max(1, min(limit, 25))  # Hard cap

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            r = await client.get(
                f"{LOTLYTICS_API}/api/v1/regions/list",
                headers=api_headers()
            )
            if r.status_code != 200:
                return "Failed to fetch market list."
            all_markets = r.json()
    except httpx.HTTPError:
        return "Failed to fetch market list — network error."

    if state:
        state_abbr = normalize_state(state)
        all_markets = [m for m in all_markets if m["regionId"].endswith(f"-{state_abbr}")]

    total_available = len(all_markets)
    SAMPLE_SIZE = 20  # Balanced: responsive + representative
    sample = all_markets[:SAMPLE_SIZE]

    # Semaphore-limited concurrent fetches — keep low to avoid rate limiting our own backend
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
        sampling_note = f"\n⚠️ Sampled {SAMPLE_SIZE} of {total_available} available markets for performance. Filter by state for more complete results.\n"

    lines = [f"**Top {len(results)} markets matching your criteria:**\n{sampling_note}"]
    for r in results:
        lines.append(
            f"**{r['name']}**\n"
            f"Price: {format_currency(r['price'])} | Appreciation: {format_pct(r['appreciation'])} | "
            f"Yield: {r['yield']}% | P/I: {r['p2i']}x | Momentum: {r['momentum']}\n"
        )

    return "\n".join(lines) + PREMIUM_WATERMARK


# ---------------------------------------------------------------------------
# Health check middleware (for Railway/hosted deployments)
# ---------------------------------------------------------------------------

def add_health_endpoint(mcp_server: FastMCP):
    """Inject a /health route into the FastMCP SSE app."""
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.responses import JSONResponse

    original_sse_app = mcp_server.sse_app

    def patched_sse_app(mount_path=None):
        app = original_sse_app(mount_path)
        async def health(request):
            return JSONResponse({"status": "ok", "service": "lotlytics-mcp"})
        # Prepend /health route
        app.routes.insert(0, Route("/health", health))
        return app

    mcp_server.sse_app = patched_sse_app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["free", "premium"], default=os.environ.get("MCP_MODE", "free"))
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    port = int(os.environ.get("PORT", args.port))

    if args.mode == "premium" and not LOTLYTICS_API_KEY:
        print("WARNING: LOTLYTICS_API_KEY not set — premium mode will make unauthenticated API calls", flush=True)

    server = free_mcp if args.mode == "free" else premium_mcp
    print(f"Starting Lotlytics {args.mode.title()} MCP (transport={args.transport}, port={port})")

    if args.transport in ("sse", "streamable-http"):
        add_health_endpoint(server)

    server.run(transport=args.transport)
