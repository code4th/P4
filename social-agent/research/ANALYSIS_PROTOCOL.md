# ANALYSIS_PROTOCOL.md

## Hourly Scan Protocol

For each run:

1. Gather major recent news and market moves from credible primary or mainstream sources.
   - If `TAVILY_API_KEY` is set, prefer `scripts/tavily_search.py`.
   - If not, fall back to `scripts/fetch_rss_news.py` and direct fetches of known public URLs.
2. Separate observation from interpretation.
3. Identify structural drivers:
   - institutions
   - capital and rates
   - labor and demographics
   - technology and media
   - culture and religion
   - geopolitics and state power
4. Compare with historical analogues when useful.
5. Note financial correlations:
   - equities
   - rates
   - FX
   - commodities
   - crypto if relevant
6. Distill recurring mechanisms into `research/STRUCTURES.md`.
7. Append historical parallels to `research/HISTORY_NOTES.md`.

## Output Shape

- New events
- Structural reading
- Historical parallels
- Financial correlations
- Durable hypotheses
- Open questions

## Notes

- OpenClaw's native `web_search` needs provider API keys.
- Tavily is not a native OpenClaw web_search provider in this installation, so use the helper script path.
