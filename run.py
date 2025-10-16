#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reddit_thread_scraper.py
Scrape a Reddit thread via its .json endpoint and extract:
- Main post title & body
- Comments likely describing selling a service (esp. AI agents/automation) with QUANTITATIVE evidence
Outputs: CSV and Markdown.

New in this version:
- Hard requirement (by default) that a comment includes *numbers*: money OR other quantitative metrics
  (hours/days/weeks/months/years, counts like clients/leads, or percentages).
- CLI flag --allow_no_numbers to relax the requirement if needed.

Usage:
    python reddit_thread_scraper.py <reddit_thread_url_or_json_url> [--out OUT_BASENAME] [--min_score 4]
    # Enforce numbers (default):
    python reddit_thread_scraper.py "<thread_url>" --min_score 4
    # Optional: allow comments with no numbers (not recommended):
    python reddit_thread_scraper.py "<thread_url>" --allow_no_numbers
"""

import argparse
import csv
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# --- Heuristics: money ---
RE_MONEY = re.compile(
    r"""
    (?:
        (?P<currency>[$€£])
        \s*
    )?
    (?P<number>\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?|\d+(?:[.,]\d+)?)
    \s*
    (?P<suffix>[kKmMbB]|million|millions|thousand|k)?
    \s*
    (?P<per>/\s*(?:h|hr|hour|day|week|mo|month|yr|year|per\s*(?:hour|day|week|month|year)))?
    """,
    re.VERBOSE
)

# --- Heuristics: other quantitative evidence ---
RE_DURATION = re.compile(r"\b\d{1,4}(?:[.,]\d+)?\s*(?:hours?|hrs?|h|days?|d|weeks?|w|months?|mos?|mo|years?|yrs?)\b", re.I)
RE_RATE = re.compile(r"\b\d{1,4}(?:[.,]\d+)?\s*(?:per|/)\s*(?:hour|hr|day|week|month|mo|year|yr)s?\b", re.I)
RE_COUNT = re.compile(r"\b\d{1,4}\s*(?:clients?|customers?|users?|leads?|emails?|meetings?|calls?|tickets?|demos?)\b", re.I)
RE_PERCENT = re.compile(r"\b\d{1,3}(?:[.,]\d+)?\s*%\b")

# Other topic signals
KEYWORDS_SELL = [
    "client", "clients", "customer", "customers", "sold", "selling", "sell",
    "paying", "paid", "charge", "charged", "pricing", "price", "subscription",
    "contract", "invoice", "retainer", "freelance", "agency", "MRR", "ARR"
]

KEYWORDS_AGENT = [
    "agent", "ai agent", "automation", "autonomous", "bot", "assistant",
    "workflow", "rpa", "gpt", "langchain", "autogen"
]

MARKET_HINTS = [
    "ecom", "e-commerce", "shopify", "woocommerce", "amazon seller", "smb",
    "dentists", "lawyers", "real estate", "realtors", "restaurants",
    "plumbers", "roofers", "contractors", "saas", "startups", "enterprise",
    "agencies", "healthcare", "doctors", "clinics", "education", "coaches",
    "course creators", "marketing", "sales", "support", "customer support",
    "helpdesk", "hotel", "hospitality", "travel", "logistics"
]

def to_json_url(url: str) -> str:
    if url.endswith(".json"):
        return url
    if not url.endswith("/"):
        url += "/"
    return url + ".json"

def http_get(url: str, retries: int = 3, backoff: float = 1.2) -> bytes:
    last_err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read()
        except Exception as e:
            last_err = e
            time.sleep(backoff * (i + 1))
    raise last_err

def load_thread(url: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    data = http_get(url)
    parsed = json.loads(data.decode("utf-8"))
    if not isinstance(parsed, list) or len(parsed) < 2:
        raise ValueError("Unexpected Reddit JSON format")
    post_listing = parsed[0]["data"]["children"][0]["data"]
    comments_listing = parsed[1]["data"]["children"]
    return post_listing, comments_listing

def flatten_comments(children: List[Dict[str, Any]], depth=0) -> List[Dict[str, Any]]:
    rows = []
    for ch in children:
        kind = ch.get("kind")
        data = ch.get("data", {})
        if kind == "t1":  # comment
            rows.append({**data, "_depth": depth})
            replies = data.get("replies")
            if isinstance(replies, dict):
                rows.extend(flatten_comments(replies.get("data", {}).get("children", []), depth + 1))
        elif kind == "more":
            continue
    return rows

def has_quantitative(text: str) -> Tuple[bool, Dict[str, List[str]]]:
    """Returns True if the text includes money OR other quantitative metrics."""
    money = [m.group(0) for m in RE_MONEY.finditer(text)]
    duration = [m.group(0) for m in RE_DURATION.finditer(text)]
    rate = [m.group(0) for m in RE_RATE.finditer(text)]
    count = [m.group(0) for m in RE_COUNT.finditer(text)]
    percent = [m.group(0) for m in RE_PERCENT.finditer(text)]
    any_num = bool(money or duration or rate or count or percent)
    return any_num, {
        "money": money, "duration": duration, "rate": rate, "count": count, "percent": percent
    }

def score_comment(body: str) -> Tuple[int, Dict[str, Any]]:
    text = body.lower()
    reasons = []
    score = 0

    if any(k in text for k in KEYWORDS_SELL):
        score += 2
        reasons.append("selling_keywords")

    if any(k in text for k in KEYWORDS_AGENT):
        score += 2
        reasons.append("agent_keywords")

    money_matches = list(RE_MONEY.finditer(text))
    if money_matches:
        score += min(3, len(money_matches))
        reasons.append("money_mention")

    markets = [m for m in MARKET_HINTS if m in text]
    if markets:
        score += 1
        reasons.append("market_hint")

    if any(w in text for w in [" we ", " i ", " my ", " our ", "case study", "story"]):
        score += 1
        reasons.append("narrative_signal")

    details = {
        "money_spans": [m.group(0) for m in money_matches],
        "markets_found": markets
    }
    return score, details

def normalize_money(token: str) -> Optional[str]:
    m = RE_MONEY.search(token.lower())
    if not m:
        return None
    num = m.group("number").replace(",", "").replace(" ", "")
    try:
        val = float(num)
    except:
        return token
    suffix = m.group("suffix")
    currency = m.group("currency") or ""
    per = m.group("per")
    if suffix and suffix.lower().startswith("m"):
        val *= 1_000_000
    elif suffix and (suffix.lower().startswith("k") or "thousand" in suffix.lower()):
        val *= 1_000
    base = f"{currency}{int(val) if float(val).is_integer() else round(val, 2)}"
    if per:
        base += " " + per.replace("/", "").strip()
    return base

def extract_best_money(money_spans: List[str]) -> Optional[str]:
    if not money_spans:
        return None
    best = None
    best_val = -1.0
    for tok in money_spans:
        m = RE_MONEY.search(tok.lower())
        if not m:
            continue
        num = m.group("number").replace(",", "").replace(" ", "")
        try:
            val = float(num)
        except:
            continue
        suf = m.group("suffix")
        if suf and suf.lower().startswith("m"):
            val *= 1_000_000
        elif suf and (suf.lower().startswith("k") or "thousand" in suf.lower()):
            val *= 1_000
        if val > best_val:
            best_val = val
            best = tok
    return normalize_money(best) if best else None

def summarize_text(text: str, limit: int = 240) -> str:
    clean = " ".join(text.split())
    return clean[:limit] + ("…" if len(clean) > limit else "")

def maybe_llm_refine(rows: List[Dict[str, Any]], thread_title: str) -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return
    try:
        import json as _json
        import urllib.request as _ureq

        system = (
            "You are a concise analyst. For each Reddit comment, if it clearly describes selling an AI agent "
            "or automation service, extract: target_market (who buys), service_description (what they sell), "
            "and revenue (if stated). Return JSON array with the fields for each input item. Use 'unknown' if unclear."
        )

        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        chunk = []
        mapping = []
        for i, row in enumerate(rows):
            payload = {"role": "user", "content": f"Thread: {thread_title}\nComment: {row['body']}\n"}
            chunk.append(payload)
            mapping.append(i)

        if not chunk:
            return

        body = _json.dumps({
            "model": "gpt-4o-mini",
            "messages": [{"role":"system","content":system}] + chunk,
            "temperature": 0.2
        }).encode("utf-8")

        req = _ureq.Request(url, data=body, headers=headers, method="POST")
        with _ureq.urlopen(req, timeout=40) as resp:
            resp_json = _json.loads(resp.read().decode("utf-8"))

        content = resp_json["choices"][0]["message"]["content"]
        try:
            arr = _json.loads(content)
            if isinstance(arr, list):
                for i, item in enumerate(arr):
                    idx = mapping[i]
                    rows[idx]["target_market"] = item.get("target_market", rows[idx]["target_market"])
                    rows[idx]["service_description"] = item.get("service_description", rows[idx]["service_description"])
                    if not rows[idx]["extracted_revenue"] and item.get("revenue"):
                        rows[idx]["extracted_revenue"] = item["revenue"]
        except Exception:
            pass
    except Exception:
        return

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url", help="Reddit thread URL (normal or .json)")
    ap.add_argument("--out", default="reddit_thread", help="Output basename (no extension)")
    ap.add_argument("--min_score", type=int, default=4, help="Minimum heuristic score to keep a comment")
    ap.add_argument("--allow_no_numbers", action="store_true", help="Do NOT require numeric evidence in comments")
    args = ap.parse_args()

    json_url = to_json_url(args.url)
    post, comments_listing = load_thread(json_url)
    title = html.unescape(post.get("title", "").strip())
    selftext = html.unescape(post.get("selftext", "").strip())
    subreddit = post.get("subreddit")
    thread_permalink = "https://www.reddit.com" + post.get("permalink", "")
    created = datetime.utcfromtimestamp(post.get("created_utc", 0)).isoformat() + "Z"
    author = post.get("author")

    flat = flatten_comments(comments_listing)
    rows = []
    for c in flat:
        body = html.unescape(c.get("body", "") or "")
        if not body.strip():
            continue

        # Hard requirement: quantitative evidence unless --allow_no_numbers supplied
        has_nums, num_details = has_quantitative(body)
        if not args.allow_no_numbers and not has_nums:
            continue

        score, details = score_comment(body)
        if score < args.min_score:
            continue

        revenue_norm = extract_best_money(details["money_spans"])
        rows.append({
            "score": score,
            "reasons": ",".join(sorted(set(
                (['money_mention'] if details.get('money_spans') else []) +
                (['market_hint'] if details.get('markets_found') else []) +
                (['has_numbers'] if has_nums else [])
            ))),
            "extracted_revenue": revenue_norm or "",
            "target_market": ", ".join(details["markets_found"]) if details["markets_found"] else "",
            "service_description": "",
            "quantitative_evidence": "; ".join(
                f"{k}:{', '.join(v)}" for k, v in num_details.items() if v
            ),
            "comment_id": c.get("id"),
            "author": c.get("author"),
            "ups": c.get("ups"),
            "permalink": "https://www.reddit.com" + c.get("permalink", ""),
            "body": body
        })

    maybe_llm_refine(rows, title)

    csv_path = f"{args.out}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "thread_title","subreddit","thread_author","thread_url","thread_created_utc",
            "comment_score","comment_author","comment_ups","extracted_revenue",
            "target_market","service_description","quantitative_evidence","permalink","body"
        ])
        for r in rows:
            w.writerow([
                title, subreddit, author, thread_permalink, created,
                r["score"], r["author"], r["ups"], r["extracted_revenue"],
                r["target_market"], r["service_description"], r.get("quantitative_evidence",""), r["permalink"], r["body"]
            ])

    md_path = f"{args.out}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        if selftext:
            f.write(f"> {selftext}\n\n")
        f.write(f"- Subreddit: r/{subreddit}\n- Author: u/{author}\n- URL: {thread_permalink}\n- Created: {created}\n\n")
        f.write("## Interesting replies (filtered)\n\n")
        for r in sorted(rows, key=lambda x: (-x["score"], -(x["ups"] or 0))):
            f.write(f"### Score {r['score']} — {r['author']} — ups: {r['ups']}\n")
            if r["extracted_revenue"]:
                f.write(f"- **Revenue:** {r['extracted_revenue']}\n")
            if r["target_market"]:
                f.write(f"- **Target market:** {r['target_market']}\n")
            if r["service_description"]:
                f.write(f"- **Service:** {r['service_description']}\n")
            if r.get("quantitative_evidence"):
                f.write(f"- **Quantitative evidence:** {r['quantitative_evidence']}\n")
            f.write(f"- **Permalink:** {r['permalink']}\n\n")
            f.write(f"{summarize_text(r['body'], 1000)}\n\n---\n\n")

    print(f"Wrote: {csv_path} and {md_path}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
