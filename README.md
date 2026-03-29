# Lotlytics MCP Server

> Real estate market data for any MCP-compatible AI assistant — Claude, Cursor, Windsurf, and more.

Ask your AI assistant questions like:
- *"Is Nashville a good market to invest in right now?"*
- *"Compare Austin vs Charlotte for rental investing"*
- *"Find markets in Texas under $300k with yield above 5%"*
- *"What's the investment health score for Phoenix AZ?"*

---

## Quick Start (Free — No account needed)

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

## Premium (Investor plan — All 895 markets)

```json
{
  "mcpServers": {
    "lotlytics": {
      "type": "sse",
      "url": "https://lotlytics-mcp-premium-production.up.railway.app/sse",
      "headers": {
        "Authorization": "Bearer YOUR_API_KEY"
      }
    }
  }
}
```

Get your API key at [lotlytics.us/mcp](https://lotlytics.us/mcp) after upgrading to Investor ($79/mo).

---

## Tools

### Free tier (top 50 US markets)

| Tool | Description |
|------|-------------|
| `get_market_summary` | Median price, appreciation, rental yield, mortgage estimate, momentum |
| `list_markets` | List available markets, filterable by state |
| `get_market_health` | Investment health score (1–10) with key signals |

### Premium tier (all 895 markets, Investor plan)

| Tool | Description |
|------|-------------|
| `get_market_summary_premium` | Full report with HUD Fair Market Rents, migration trends, affordability score, percentile rankings |
| `compare_markets` | Side-by-side comparison of two cities across 10+ metrics |
| `search_markets` | Filter 895 markets by price range, rental yield, appreciation, and affordability |

---

## Free Tier Markets

Top 50 US metros including: Austin TX, Nashville TN, Phoenix AZ, Charlotte NC, Tampa FL, Denver CO, Atlanta GA, Dallas TX, Las Vegas NV, Orlando FL, and more.

---

## Running Locally

```bash
git clone https://github.com/daejung83/lotlytics-mcp
cd lotlytics-mcp
pip install -r requirements.txt
python server.py
```

Or use the hosted SSE endpoints above — no local setup needed.

---

## Links

- 🌐 **Website:** [lotlytics.us](https://lotlytics.us)
- 📖 **MCP Docs:** [lotlytics.us/mcp](https://lotlytics.us/mcp)
- 💳 **Pricing:** [lotlytics.us/pricing](https://lotlytics.us/pricing)

---

## License

MIT
