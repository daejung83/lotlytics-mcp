# Lotlytics MCP Server

> Real estate market data for any MCP-compatible AI assistant — Claude, Cursor, Windsurf, and more.

Ask your AI questions like:
- *"Is Nashville a good market to invest in right now?"*
- *"Compare Austin vs Charlotte for rental investing"*
- *"Find markets in Texas under $300k with yield above 5%"*
- *"What's the investment health score for Phoenix AZ?"*

---

## Quick Start — Free, No Account Needed

Add to your Claude Desktop config:

**Mac:** `~/Library/Application Support/Claude/claude_desktop_config.json`  
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "lotlytics": {
      "type": "sse",
      "url": "https://lotlytics-mcp-production.up.railway.app/sse"
    }
  }
}
```

Restart Claude Desktop. Look for the 🔌 plugin icon. Done.

---

## Premium — Investor Plan (All 895 Markets)

Same URL, just add your API key as a header:

```json
{
  "mcpServers": {
    "lotlytics": {
      "type": "sse",
      "url": "https://lotlytics-mcp-production.up.railway.app/sse",
      "headers": {
        "X-API-Key": "YOUR_API_KEY"
      }
    }
  }
}
```

Get your API key at [lotlytics.us/settings/api-keys](https://lotlytics.us/settings/api-keys) after upgrading to Investor ($79/mo).

---

## Free vs Premium

| | Free | Premium (Investor $79/mo) |
|---|---|---|
| API key required | No | Yes (`X-API-Key` header) |
| Markets | Top 50 US metros | All 895 US metros |
| Requests/day | 20 per IP | 1,000 per key |

---

## Tools

### Free tier (top 50 markets, no key needed)

| Tool | Description |
|------|-------------|
| `get_market_health` | Investment health score (1–10) with key signals |
| `get_market_summary` | Prices, appreciation, rental yield, mortgage estimate, momentum |
| `list_markets` | List available markets, filterable by state |

### Premium tier (all 895 markets, requires `X-API-Key`)

| Tool | Description |
|------|-------------|
| `get_market_summary` | Everything above + HUD Fair Market Rents, percentile rankings, full migration data |
| `compare_markets` | Side-by-side comparison of two cities across 10+ metrics |
| `search_markets` | Filter 895 markets by price, yield, appreciation, affordability |

---

## Running Locally

```bash
git clone https://github.com/daejung83/lotlytics-mcp
cd lotlytics-mcp
pip install -r requirements.txt
python server.py --transport sse --port 8080
```

Or use the hosted SSE endpoint above — no local setup needed.

---

## Links

- 🌐 **Website:** [lotlytics.us](https://lotlytics.us)
- 📖 **MCP Docs:** [lotlytics.us/mcp](https://lotlytics.us/mcp)
- 💳 **Pricing:** [lotlytics.us/pricing](https://lotlytics.us/pricing)

---

## License

MIT
