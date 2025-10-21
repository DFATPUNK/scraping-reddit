# Scraping Reddit

## Overview

This repo contains **two complementary scripts** to collect “social proof” from Reddit about AI agents (revenues, stack/tools, niches/clients), score the messages, and export the results:

* **`scrape_reddit_agents.py`** — **multi-subreddit “discovery”**: searches several subreddits with multiple queries, extracts comments that mention a **revenue** (mandatory), computes a **weighted 0–100 score**, exports to **CSV/Markdown**, and optionally:

  * **pushes rows to a Notion database**,
  * **publishes the files to public URLs** (GitHub **Gist** or **Repo**) and **adds them as file blocks** to a Notion page/block.

* **`run.py`** — **single-thread audit**: given one Reddit thread URL, extracts & scores relevant comments from that thread only (quicker & focused).

> ⏱️ Typical runtime for `scrape_reddit_agents.py`: **~500–1000 seconds** (depends on Reddit API, number of threads/comments, and backoff). That’s expected.

---

## What is extracted?

A comment is kept only if it contains a **revenue mention**. For each kept comment we export:

* `Subreddit`
* `Thread URL`
* `Comment URL`
* `Author`
* `Post` (original message)
* `Score` (0–100)

Results are **sorted by descending score** in both CSV and Markdown.

---

## Scoring (0–100, weighted)

A higher score means a stronger business signal:

* **Revenue (0–55)**
  Presence (+25), **recurrence** (+15 week / +12 month / +10 day / +8 year / +6 other), **precision** (+10 exact, +5 approx/range), **currency** (+5).
* **Market/Client (0–20)**
  Niche/segment via “for/pour/to help/…” (+10), explicit client/company/role (+10).
* **Stack/Services (0–15)**
  Mentions of tools (OpenAI, Claude, LangChain, CrewAI, n8n, Zapier, etc.), +3 each, capped at +15.
* **Sentiment/Outcome (0–10)**
  Success (+10), doubtful/neutral (+5), **failure** (explicit “no sales / failed to monetize”) → **score forced to 0**.

---

## Repository layout

```
.
├── scrape_reddit_agents.py     # multi-subreddit discovery, scoring, CSV/MD, Notion, public upload
├── run.py                      # single-thread audit
├── requirements.txt
└── README.md
```

---

## Installation

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

---

## Environment variables

Create a `.env` file at repo root (recommended).

### Reddit (required)

```
REDDIT_CLIENT_ID=...
REDDIT_CLIENT_SECRET=...
REDDIT_USER_AGENT=ai-agents-scraper by u/<your_username>
REDDIT_USERNAME=...
REDDIT_PASSWORD=...
```

### Notion (optional)

```
NOTION_API_KEY=secret_xxx
NOTION_DATABASE_ID=29380d175a28805fa550cb5c99df7ded   # if you use --notion
NOTION_BLOCK_ID=29380d175a288090a65bc99cef979ab3       # if you use --notion-files
```

### Public upload (optional: choose **one** target)

**GitHub Gist**

```
GITHUB_TOKEN=ghp_...                 # scope: gist
GIST_DESCRIPTION=Reddit scraping exports   # optional
```

**GitHub Repo**

```
GITHUB_TOKEN=ghp_...                 # scope: repo
GITHUB_REPO=DFATPUNK/scraping-reddit # owner/repo
GITHUB_BRANCH=main                   # optional (default: main)
GITHUB_PATH_PREFIX=scraping          # optional subfolder (default: scraping)
```

---

## Usage

### A) Multi-subreddit discovery — `scrape_reddit_agents.py`

**Default (local exports only)**

```bash
python scrape_reddit_agents.py
```

Produces:

* `reddit_ai_agents.csv`
* `reddit_ai_agents.md`

**Also push rows to a Notion database**

```bash
python scrape_reddit_agents.py --notion
```

* Creates one Notion page per result in `NOTION_DATABASE_ID`.
* Note: Notion requires a Title property (the script uses a “Name” title field).

**Publish files to public URLs and attach them to a Notion block**

Using **Gist**:

```bash
python scrape_reddit_agents.py --upload-target gist --notion-files
```

Using **GitHub Repo**:

```bash
python scrape_reddit_agents.py --upload-target repo --notion-files
```

**Do everything (DB + public files + attach in Notion)**

```bash
# via Gist
python scrape_reddit_agents.py --notion --upload-target gist --notion-files

# via GitHub Repo
python scrape_reddit_agents.py --notion --upload-target repo --notion-files
```

**Notes**

* If `--notion-files` is set, the script **adds two external file blocks** in the Notion block `NOTION_BLOCK_ID`, pointing to the **public** URLs of `reddit_ai_agents.md` and `reddit_ai_agents.csv` (either Gist raw URLs or GitHub raw URLs).
* Subreddits that **disallow API search** return HTTP 404; the script logs and skips them automatically.

---

### B) Single-thread audit — `run.py`

Analyze one specific Reddit thread you already know:

```bash
python run.py --url "https://www.reddit.com/r/AI_Agents/comments/XXXXXXXX/..."
```

Outputs the same two files (but limited to that thread):

* `reddit_ai_agents.csv`
* `reddit_ai_agents.md`

> If you extended `run.py` with the same flags (`--notion`, `--upload-target`, `--notion-files`), you can use them similarly. Otherwise, keep it minimal as above.

---

## Output examples

Check examples derived from [this Reddit thread](https://www.reddit.com/r/AI_Agents/comments/1l3rmp6/anyone_here_actually_making_money_selling_ai/):

* [`reddit_thread.csv`](https://github.com/DFATPUNK/scraping-reddit/blob/main/reddit_thread.csv)
* [`reddit_thread.md`](https://github.com/DFATPUNK/scraping-reddit/blob/main/reddit_thread.md)

---

## Performance

* `scrape_reddit_agents.py` typically takes **~500–1000 seconds** (8–16 minutes).
  This depends on Reddit API availability, the number of threads and comments, and built-in backoff for robustness.

---

## Troubleshooting

* **404 during subreddit search**
  That subreddit likely **disables API search** (or has restrictions). The script logs an info/warn and continues.

* **Nothing appears in Notion**

  * Check `NOTION_API_KEY` and `NOTION_DATABASE_ID`.
  * Share the database with your integration in Notion (“Share” → invite your integration).
  * Ensure your DB has a **Title** property (used as “Name”).

* **Files not added to Notion block**

  * You must pass `--notion-files` and define `NOTION_BLOCK_ID`.
  * Public upload must succeed first (Gist or Repo) so the script has URLs to attach.

* **GitHub uploads fail**

  * Gist: `GITHUB_TOKEN` must have `gist` scope.
  * Repo: `GITHUB_TOKEN` must have `repo` scope and write access to `GITHUB_REPO`.
  * Check `GITHUB_BRANCH` and `GITHUB_PATH_PREFIX` (the script creates/updates files at that path).

---

## Contact

Email: [jeremy@jeremybrunet.com](mailto:jeremy@jeremybrunet.com)