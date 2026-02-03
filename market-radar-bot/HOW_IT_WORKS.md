# How Market Radar Bot Works

**A Plain English Explanation**

---

## The Big Picture

Imagine you have a team of assistants who:
1. Read hundreds of news sources every minute
2. Know exactly what topics you care about (Fed decisions, Trump tariffs, oil prices, etc.)
3. Immediately alert you when something important happens
4. Include the exact quote from the article so you know it's real

That's what Market Radar Bot does, automatically.

---

## The Problem It Solves

**Before Market Radar:**
- You manually check Reuters, Bloomberg, Fed website, CNBC, etc.
- You might miss an important announcement while sleeping or busy
- By the time you see the news, markets have already moved
- You waste time reading irrelevant articles

**After Market Radar:**
- News sources are checked every 60 seconds (configurable)
- Important news is instantly sent to your Telegram
- Each alert includes severity score so you know how urgent it is
- You only get notified about what YOU told it to watch

---

## How It Works (Step by Step)

### Step 1: Watching the News

The bot continuously monitors news sources you configure:

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  Fed Website    │    │  Reuters RSS    │    │  CNBC News      │
│  (Primary)      │    │  (Primary)      │    │  (Secondary)    │
└────────┬────────┘    └────────┬────────┘    └────────┬────────┘
         │                      │                      │
         └──────────────────────┴──────────────────────┘
                                │
                                ▼
                    ┌─────────────────────┐
                    │   MARKET RADAR BOT  │
                    │   (Checking every   │
                    │    60 seconds)      │
                    └─────────────────────┘
```

It's smart about this:
- It remembers what it already saw (doesn't process the same article twice)
- It only downloads new articles (saves bandwidth)
- If a source is down, it just skips it and tries again later

### Step 2: Your Watchlist

You tell the bot what to watch for. Example watchlist:

| What to Watch | Keywords | Why You Care |
|---------------|----------|--------------|
| Federal Reserve | fed, fomc, jerome powell, interest rate | Rate decisions move everything |
| Donald Trump | trump, tariff, trade war | Tariffs affect markets globally |
| OPEC | opec, oil production, saudi | Oil prices |
| US CPI | cpi, inflation, consumer price | Key economic indicator |

Each item has:
- **Name**: "Federal Reserve"
- **Keywords**: Words that might appear in articles about this topic
- **Entities**: Specific names/terms (matched more precisely)
- **Assets Affected**: What markets might move (USD, S&P 500, Gold)

### Step 3: Matching Articles to Your Watchlist

When a new article comes in, the bot asks: "Does this match anything on my watchlist?"

**Example Article:**
> "Fed Chair Jerome Powell says interest rates will remain elevated as inflation persists above the 2% target."

**Matching Process:**
1. ✓ Found "Jerome Powell" (entity match)
2. ✓ Found "Fed" (keyword match)
3. ✓ Found "interest rate" (keyword match)
4. ✓ Found "inflation" (keyword match)
5. **Result**: Strong match to "Federal Reserve" watchlist item

**The Key Quote:**
The bot extracts the exact sentence that triggered the match:
> "...Jerome Powell says interest rates will remain elevated as inflation persists..."

This quote goes into your alert, so you know exactly what triggered it.

### Step 4: Scoring Severity (How Important Is This?)

Not all news is equally important. The bot calculates a severity score from 0-100:

```
SEVERITY SCORE BREAKDOWN:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                                              Points
Match Confidence (how well it matched)         0-40
Source Quality (Fed website vs random blog)    0-20
Recency (how fresh the news is)               0-15
High-Impact Keywords (rate hike, war, etc.)    0-25
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TOTAL                                         0-100
```

**Example Scoring:**
- Fed website article about rate hike from 30 minutes ago
- Strong keyword/entity matches

| Component | Score |
|-----------|-------|
| Match Confidence (strong match) | 35 |
| Source Tier (Fed = Primary) | 20 |
| Recency (30 min ago) | 15 |
| Keywords ("rate hike" = high impact) | 15 |
| **Total** | **85** |

This is a high score → **Alert!**

### Step 5: Deciding Whether to Alert

The bot uses thresholds:

| Severity Score | What Happens |
|----------------|--------------|
| 0-29 | Ignored (logged only) |
| 30-69 | Saved for potential digest (future feature) |
| 70-100 | **Immediate Telegram Alert** |
| 90-100 | Alert + **Bypasses Cooldown** |

**Cooldown Explained:**
To avoid alert fatigue, the bot waits 15 minutes (configurable) between alerts for the same topic. So if there are 5 articles about the same Fed announcement, you get one alert, not five.

Exception: If something scores 90+, it's so important that cooldown is ignored.

### Step 6: The Alert You Receive

Your Telegram alert looks like this:

```
🚨 Fed Chair Powell: Interest Rates to Remain Elevated

📊 Watch Item: Federal Reserve
📁 Category: central_bank
🎯 Severity: 85/100
💯 Confidence: 87%
💰 Assets: USD, S&P 500, US Treasuries, Gold

📝 Key Quote(s):
"Jerome Powell says interest rates will remain elevated
as inflation persists above the 2% target."

🔗 Source: federalreserve.gov/...
🕐 2024-01-15 14:30 UTC / 15:30 Berlin
```

**What Each Part Means:**
- **Severity**: How important (85 = very)
- **Confidence**: How sure the bot is about the match (87%)
- **Assets**: What might be affected
- **Key Quote**: The exact text that triggered it (not AI-generated!)
- **Source**: Direct link to the original article

---

## Why This Approach?

### 1. Rules-First, AI-Second

Most of the matching is done with simple keyword/entity matching. This is:
- Fast (milliseconds)
- Predictable (same input = same output)
- Free (no API costs)

AI (OpenRouter/Claude) is only used as a backup when rules aren't confident enough. This means:
- The system works even without an AI API key
- You're not paying per alert
- Results are explainable

### 2. No Hallucinated Information

Every alert includes a **real quote from the source**. The bot:
1. Finds matching text in the article
2. Extracts the exact substring
3. Verifies it's really in the article
4. Only then includes it in the alert

If using AI for matching, the AI must provide exact quotes that exist in the article. If it can't, the match is rejected.

### 3. Tiered Source Trust

Not all news sources are equal:

| Tier | Examples | Trust Level |
|------|----------|-------------|
| Primary | Fed.gov, ECB.eu, Official sources | Highest (+20 to severity) |
| Secondary | Reuters, Bloomberg, WSJ | Medium (+10 to severity) |
| Social | ZeroHedge, Twitter, Blogs | Lower (+0 to severity) |

Same news from Fed.gov scores higher than same news from a random blog.

### 4. Deduplication

The same story appears on 50 news sites. Without dedup, you'd get 50 alerts.

The bot prevents this by:
1. Hashing each article's key info (URL + title + date)
2. Checking if that hash already exists
3. Skipping if it's a duplicate

Also checks for "near-duplicates" (same story, slightly different headline).

---

## What You Can Customize

### In the Admin Panel:

**Watch Items** (what to monitor):
- Add people (CEOs, politicians, central bankers)
- Add institutions (Fed, IMF, OPEC)
- Add event types (CPI releases, FOMC meetings)
- Add megatrends (de-dollarization, tech war)
- Set custom keywords and entities
- Set custom severity rules
- Set cooldown per item

**Sources** (where to monitor):
- Add RSS feeds
- Add web pages to scrape
- Set source tier (primary/secondary/social)
- Mark sources as global (checked for all items) or linked to specific items

**Settings**:
- Alert threshold (default: 70)
- Polling interval (default: 60 seconds)
- Default cooldown (default: 15 minutes)

---

## Example Scenarios

### Scenario 1: Fed Rate Decision

1. Fed posts press release to their RSS feed
2. Bot fetches it within 60 seconds
3. Matches: "Federal Reserve" + "interest rate" + "FOMC"
4. Extracts key quote about the decision
5. Scores: 90+ (official source, rate decision, very recent)
6. Sends immediate alert to Telegram
7. You see it on your phone within 2 minutes of announcement

### Scenario 2: Trump Tariff Tweet

1. News outlets report on Trump tariff statement
2. Bot fetches from CNBC RSS
3. Matches: "Trump" + "tariff"
4. Scores: 75 (secondary source, high-impact keyword)
5. Sends alert
6. 10 minutes later, same story on WSJ → **blocked by dedup**
7. You get one alert, not ten

### Scenario 3: Routine ECB Meeting

1. ECB posts routine meeting notes
2. Bot matches to "European Central Bank"
3. No high-impact keywords (no rate change, no surprise)
4. Scores: 55 (below threshold)
5. **No alert** (logged in database, visible in admin panel)

---

## The Technology Behind It

For the curious:

| Component | Technology | Why |
|-----------|------------|-----|
| Web Framework | FastAPI (Python) | Fast, modern, easy to extend |
| Database | SQLite | Simple, no setup, works everywhere |
| RSS Parsing | feedparser | Industry standard for RSS |
| Web Scraping | BeautifulSoup | Reliable HTML parsing |
| Scheduling | APScheduler | Background job management |
| Notifications | Telegram Bot API | Instant, free, reliable |
| AI (Optional) | OpenRouter API | Access to Claude, GPT, etc. |

---

## Security Considerations

- Admin panel is password-protected
- Sessions expire after 24 hours
- No sensitive data logged
- Database is local (not cloud)
- Telegram token stored only in .env file
- AI API key (if used) never logged

---

## The Learning System

The bot gets smarter over time! Here's how:

### How It Learns

After sending an alert, the bot watches what actually happens to the market:

```
Alert Sent         →    5 min later    →    15 min later    →    1 hour later
"Fed announces           Price check         Price check          Price check
rate hike"               Did market move?    Did market move?     Did market move?
```

### Why This Matters

**The Problem:**
- Some sources exaggerate ("BREAKING: Minor comment!")
- Some alerts sound important but markets don't care
- You want to focus on sources that actually predict market moves

**The Solution:**
The bot tracks which sources are reliable:

| Source | High Alerts | Actually Moved Market | Reliability |
|--------|------------|----------------------|-------------|
| Fed.gov | 15 | 14 (93%) | ★★★★★ Very High |
| Reuters | 50 | 38 (76%) | ★★★★☆ High |
| Random Blog | 30 | 8 (27%) | ★★☆☆☆ Low |

### Automatic Adjustments

Based on track record:
- **Reliable sources**: Severity score gets boosted (+10 to +20)
- **Unreliable sources**: Severity score gets reduced (-10 to -20)

So if "Random Blog" posts about a rate hike, the severity might go from 70 to 55 (below your alert threshold), because history shows their high-severity alerts rarely move markets.

### Finding New Sources

The bot can also discover new sources automatically:
1. Scans trusted domains for RSS feeds
2. Validates they have relevant content
3. Presents them for your approval
4. Adds them to your monitoring

### Historical Data

All this data is saved for the future:
- Every alert, what it predicted, what actually happened
- Which assets moved and in which direction
- Time of day, day of week patterns

This data can be used to train AI models that predict price movements from news!

You can access all this in the Admin Panel under "Learning".

---

## Limitations

1. **Only as good as sources**: If a source doesn't have an RSS feed or blocks scraping, we can't monitor it
2. **Not instant**: There's 60 seconds between checks (configurable)
3. **No sentiment**: Doesn't know if news is "good" or "bad", just that it's relevant
4. **English-focused**: Works best with English content
5. **No images/videos**: Only processes text content
6. **Learning takes time**: Need at least 10 alerts per source before reliability adjustments kick in

---

## Summary

Market Radar Bot = Automated news monitoring + Smart filtering + Instant alerts + Self-improving

It's like having a team that:
- Reads every news source 24/7
- Only bothers you when something you care about happens
- Includes the exact quote so you know it's real
- Learns which sources actually matter over time
- Gets smarter the more you use it
