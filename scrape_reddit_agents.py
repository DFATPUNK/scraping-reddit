import os
import re
import time
import csv
import random
import argparse
import base64
from dataclasses import dataclass
from typing import List, Optional, Dict, Tuple
from dotenv import load_dotenv
import praw
import requests
from prawcore.exceptions import RequestException, ResponseException, ServerError

load_dotenv()

# --- CLI ---
parser = argparse.ArgumentParser(description="Scrape Reddit social proof about AI agents.")
parser.add_argument("--notion", action="store_true", help="Push entries to Notion database.")
parser.add_argument("--notion-files", action="store_true", help="Append MD/CSV as file blocks to NOTION_BLOCK_ID (requires public URLs).")
parser.add_argument("--upload-target", choices=["gist", "repo"], help="Where to upload the files to get public URLs (GitHub Gist or GitHub Repo).")
args = parser.parse_args()

# --- Reddit Auth ---
reddit = praw.Reddit(
    client_id=os.getenv("REDDIT_CLIENT_ID"),
    client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
    user_agent=os.getenv("REDDIT_USER_AGENT", "ai-agents-scraper"),
    username=os.getenv("REDDIT_USERNAME"),
    password=os.getenv("REDDIT_PASSWORD"),
)

# --- Config ---
SUBREDDITS = [
    "AI_Agents", "Entrepreneur", "SaaS", "startups",
    "ArtificialIntelligence", "MachineLearning", "nocode", "automation"
]
SEARCH_QUERIES = [
    "making money AI agents",
    "selling AI agents",
    "AI agent revenue",
    "agent as a service",
    "automations $/mo",
    "monetize AI agent",
    "clients AI agent niche",
    # FR
    "vendre agent IA",
    "revenu par mois agent IA",
]
MAX_THREADS_PER_QUERY = 15
MAX_COMMENTS_PER_THREAD = 100  # pagine si besoin

# --- Notion IDs (via .env)
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")  # DB pour les entrées (si --notion)
NOTION_BLOCK_ID = os.getenv("NOTION_BLOCK_ID", "")        # Bloc pour les fichiers (si --notion-files)

# --- GitHub / Gist ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")          # owner/repo (si --upload-target repo)
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
GITHUB_PATH_PREFIX = os.getenv("GITHUB_PATH_PREFIX", "scraping").strip("/")
GIST_DESCRIPTION = os.getenv("GIST_DESCRIPTION", "Reddit scraping exports")

# --- Extraction helpers ---
CURRENCY_MAP = {"€": "EUR", "$": "USD", "£": "GBP"}
CURRENCY_CODES = {"eur", "usd", "gbp", "€", "$", "£"}

REVENUE_PATTERNS = re.compile(
    r"""
    (?P<currency>[$€£]?)\s*
    (?P<amount>\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?|\d+(?:[.,]\d+)?)
    \s*(?P<mult>[kKmM])?
    \s*(?P<unit>
        (?:/|\bper\s+|p/)?\s*
        (?:
            d(?:ay)?|jour|j|
            w(?:k|eek)?|semaine|sem|
            mo(?:nth)?|month|mois|m|
            y(?:r|ear)?|an|année|ans
        )
    )?
    """,
    re.VERBOSE
)

SERVICE_CUES = [
    "openai", "assistants api", "gpt-4", "gpt-4o", "anthropic", "claude",
    "cohere", "langchain", "llamaindex", "crewai", "autogen", "openagents",
    "flowise", "n8n", "zapier", "make.com", "make (integromat)", "relevance ai",
    "agentops", "vercel ai sdk", "modal", "bedrock", "vertex ai", "hugging face",
    "groq", "ollama"
]

NICHE_PIVOTS = [
    "for ", "pour ", "to help ", "serving ", "targeting ",
    "for helping ", "aider ", "auprès des ", "with "
]

SUCCESS_CUES = [
    "paying customer", "paying customers", "mrr", "arr", "profitable",
    "sold", "closed", "booked", "recurring", "retain", "retainer",
    "works well", "working well", "it works", "fonctionne", "ça marche",
    "clients payent", "clients payants",
]
DOUBT_CUES = [
    "anyone making", "anyone here", "how to", "question", "help",
    "struggling", "trying", "explore", "exploring", "idea", "idée",
    "en cours", "hésite", "tester", "tests", "proof of concept", "poc"
]
FAIL_CUES = [
    "failed", "no sales", "can't monetize", "cannot monetize", "didn't sell",
    "aucune vente", "pas de vente", "impossible à monétiser", "échec"
]

APPROX_CUES = ["~", "≈", "about", "around", "approx", "approximately", "environ", "roughly", "presque"]
RANGE_PATTERN = re.compile(r"\b\d+(?:[.,]?\d+)?\s*[-–]\s*\d+(?:[.,]?\d+)?\b")

@dataclass
class Evidence:
    subreddit: str
    thread_title: str
    thread_url: str
    comment_url: str
    author: str
    post: str           # contenu original (message du commentaire)
    score: int          # 0..100

# --- Parsing helpers ---

def _to_float_amount(amount_str: str) -> Optional[float]:
    if not amount_str:
        return None
    s = amount_str.strip().replace(" ", "").replace("’", "").replace("˙", ".")
    if "," in s and "." in s:
        last = max(s.rfind(","), s.rfind("."))
        int_part = re.sub(r"[.,]", "", s[:last])
        frac_part = s[last+1:]
        s = f"{int_part}.{frac_part}"
    else:
        if "," in s and "." not in s:
            s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None

def normalize_revenue(m: re.Match) -> Tuple[Optional[float], Optional[str], Optional[str], str]:
    original = m.group(0).strip()
    currency_sym = m.group("currency") or ""
    currency = CURRENCY_MAP.get(currency_sym, None) if currency_sym else None

    amount_raw = (m.group("amount") or "").strip()
    amount = _to_float_amount(amount_raw)
    mult = (m.group("mult") or "").lower()
    unit = (m.group("unit") or "").lower().replace(" ", "")

    if amount is None:
        return None, currency, None, original

    if mult == "k":
        amount *= 1_000
    elif mult == "m":
        amount *= 1_000_000

    period = None
    if any(u in unit for u in ["mo", "month", "mois", "/m"]):
        period = "month"
    elif any(u in unit for u in ["wk", "w", "week", "semaine", "sem"]):
        period = "week"
    elif any(u in unit for u in ["d", "day", "jour", "j"]):
        period = "day"
    elif any(u in unit for u in ["yr", "y", "year", "an", "année", "ans"]):
        period = "year"

    return amount, currency, period, original

def find_services(text: str) -> str:
    t = text.lower()
    hits = [s for s in SERVICE_CUES if s in t]
    hits = sorted(set(hits), key=len, reverse=True)
    return ", ".join(hits)

def find_niche(text: str) -> str:
    t = " " + text.replace("\n", " ") + " "
    for p in NICHE_PIVOTS:
        idx = t.lower().find(" " + p)
        if idx != -1:
            start = idx + 1 + len(p)
            tail = t[start:]
            words = tail.split()
            snippet = " ".join(words[:12])
            snippet = re.split(r"[.?!;:()\[\]{}|/\\]", snippet)[0]
            return snippet.strip()
    return ""

def _text_has_any(text: str, cues: List[str]) -> bool:
    t = text.lower()
    return any(c in t for c in cues)

def _has_currency_code(text: str) -> bool:
    t = text.lower()
    return any(code in t for code in CURRENCY_CODES)

def _detect_period(unit_text: Optional[str], full_text: str) -> Optional[str]:
    u = (unit_text or "").lower()
    ft = (full_text or "").lower()
    if any(k in u for k in ["d", "day", "jour", "j"]) or any(k in ft for k in [" per day", "/d", "par jour"]):
        return "day"
    if any(k in u for k in ["wk", "w", "week", "semaine", "sem"]) or any(k in ft for k in [" per week", "/wk", "/w", "par semaine"]):
        return "week"
    if any(k in u for k in ["mo", "month", "mois", "m"]) or any(k in ft for k in [" per month", "/mo", "/m", "par mois"]):
        return "month"
    if any(k in u for k in ["yr", "y", "year", "an", "année", "ans"]) or any(k in ft for k in [" per year", "/yr", "/y", "par an", "par année"]):
        return "year"
    return None

def _precision_points(body: str, rev_text: str) -> int:
    approx = _text_has_any(rev_text, APPROX_CUES) or _text_has_any(body, APPROX_CUES)
    ranged = bool(RANGE_PATTERN.search(rev_text)) or bool(RANGE_PATTERN.search(body))
    if approx or ranged:
        return 5
    return 10

def _period_points(period: Optional[str]) -> int:
    if period == "week":
        return 15
    if period == "month":
        return 15
    if period == "day":
        return 15
    if period == "year":
        return 15
    return 5 if period else 0

def _market_points(body: str) -> int:
    has_niche = bool(find_niche(body))
    pts = 10 if has_niche else 0
    client_cues = [
        "client", "customer", "customers", "clients", "smb", "realtor", "realtors",
        "law firm", "lawyer", "attorney", "restaurant", "ecom", "saas", "agency", "agencies"
    ]
    named = _text_has_any(body, client_cues) or bool(
        re.search(r"\b[A-Z][A-Za-z0-9&.\-]{2,}\b(?:\s(?:Inc|LLC|Ltd|SAS|GmbH|SARL|AG)\b)", body)
    )
    pts += 10 if named else 0
    return pts

def _stack_points(body: str, title: str) -> int:
    merged = (body + " " + title).lower()
    hits = [s for s in SERVICE_CUES if s in merged]
    return min(15, 3 * len(set(hits)))

def _sentiment_points(body: str) -> int:
    t = body.lower()
    if _text_has_any(t, FAIL_CUES):
        return -999  # échec => score total 0
    if _text_has_any(t, SUCCESS_CUES):
        return 10
    if _text_has_any(t, DOUBT_CUES):
        return 5
    return 7  # défaut légèrement positif si revenu existe

def compute_score_v2(body: str, submission_title: str, rev_match: re.Match, currency: Optional[str], unit: Optional[str]) -> int:
    base = 25
    currency_pts = 5 if (currency or _has_currency_code(body)) else 0
    period = _detect_period(unit, body)
    period_pts = _period_points(period)
    precision_pts = _precision_points(body, rev_match.group(0))
    revenue_pts = base + currency_pts + period_pts + precision_pts  # max 55

    market_pts = _market_points(body)    # max 20
    stack_pts = _stack_points(body, submission_title)  # max 15

    sent = _sentiment_points(body)
    if sent < 0:
        return 0
    sentiment_pts = sent  # 0..10

    total = revenue_pts + market_pts + stack_pts + sentiment_pts
    return max(0, min(100, total))

# --- Core extraction ---

@dataclass
class Evidence:
    subreddit: str
    thread_title: str
    thread_url: str
    comment_url: str
    author: str
    post: str
    score: int

def extract_evidence(subreddit, submission, comment) -> Optional[Evidence]:
    body = comment.body if hasattr(comment, "body") else ""
    if not body:
        return None

    rev_match = REVENUE_PATTERNS.search(body)
    if not rev_match:
        return None

    value, currency, period, rev_text = normalize_revenue(rev_match)
    unit = rev_match.group("unit") if rev_match else None

    score = compute_score_v2(body, submission.title, rev_match, currency, unit)

    return Evidence(
        subreddit=subreddit.display_name if hasattr(subreddit, "display_name") else str(subreddit),
        thread_title=submission.title,
        thread_url=f"https://www.reddit.com{submission.permalink}",
        comment_url=f"https://www.reddit.com{comment.permalink}",
        author=str(comment.author) if comment.author else "[deleted]",
        post=body.strip(),
        score=score
    )

# --- Rate limit/backoff & Reddit crawling ---

def backoff_sleep(base=1.0, factor=2.0, jitter=True, attempt=1, cap=60):
    sleep_for = min(cap, base * (factor ** (attempt - 1)))
    if jitter:
        sleep_for += random.uniform(0, 0.5)
    time.sleep(sleep_for)

def search_threads() -> List[Tuple[str, str]]:
    results = []
    seen_ids = set()
    for sub in SUBREDDITS:
        sr = reddit.subreddit(sub)
        for q in SEARCH_QUERIES:
            attempt = 1
            while True:
                try:
                    for submission in sr.search(q, sort="new", limit=MAX_THREADS_PER_QUERY):
                        if submission.id not in seen_ids:
                            seen_ids.add(submission.id)
                            results.append((sub, submission.id))
                    time.sleep(0.5)
                    break
                except (ServerError, ResponseException, RequestException) as e:
                    if "404" in str(e):
                        print(f"[INFO] Skipped r/{sub}: search not allowed (404).")
                        break
                    backoff_sleep(attempt=attempt)
                    attempt += 1
                    if attempt > 5:
                        print(f"[WARN] search '{q}' on r/{sub} gave persistent errors: {e}")
                        break
    return results

def fetch_comments(submission_id: str, limit=MAX_COMMENTS_PER_THREAD):
    attempt = 1
    while True:
        try:
            submission = reddit.submission(id=submission_id)
            submission.comments.replace_more(limit=0)
            comments = submission.comments.list()
            if limit:
                comments = comments[:limit]
            return submission, comments
        except (ServerError, ResponseException, RequestException) as e:
            backoff_sleep(attempt=attempt)
            attempt += 1
            if attempt > 5:
                raise

def run_scrape() -> List[Evidence]:
    seen_comments = set()
    evidences: List[Evidence] = []
    thread_refs = search_threads()
    for sub_name, sub_id in thread_refs:
        try:
            submission, comments = fetch_comments(sub_id)
            sr = reddit.subreddit(sub_name)
            for c in comments:
                if c.id in seen_comments:
                    continue
                ev = extract_evidence(sr, submission, c)
                if ev:
                    evidences.append(ev)
                seen_comments.add(c.id)
            time.sleep(0.5)
        except Exception as e:
            print(f"[WARN] {sub_name}/{sub_id}: {e}")
            time.sleep(1.0)
    return evidences

# --- Exports ---

CSV_PATH = "reddit_ai_agents.csv"
MD_PATH = "reddit_ai_agents.md"

def export_csv(rows: List[Evidence], path=CSV_PATH):
    fieldnames = ["subreddit", "thread_url", "comment_url", "author", "post", "score"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({
                "subreddit": r.subreddit,
                "thread_url": r.thread_url,
                "comment_url": r.comment_url,
                "author": r.author,
                "post": r.post,
                "score": r.score,
            })
    print(f"[OK] CSV -> {path}")

def export_markdown(rows: List[Evidence], path=MD_PATH):
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Reddit – Preuves sociales agents IA (triées par score décroissant)\n\n")
        for r in rows:
            f.write(f"## {r.thread_title}\n")
            f.write(f"- **Score**: {r.score}\n")
            f.write(f"- **Subreddit**: r/{r.subreddit}\n")
            f.write(f"- **Thread**: {r.thread_url}\n")
            f.write(f"- **Comment**: {r.comment_url}\n")
            f.write(f"- **Auteur**: {r.author}\n")
            f.write(f"- **Post**:\n\n> {r.post}\n\n---\n\n")
    print(f"[OK] Markdown -> {path}")

# --- Uploaders (GitHub Gist / GitHub Repo) ---

def upload_files_to_gist(file_paths: List[str]) -> Dict[str, str]:
    """Return {filename: raw_url} via public Gist."""
    if not GITHUB_TOKEN:
        print("[INFO] GITHUB_TOKEN manquant; impossible de créer un Gist.")
        return {}
    url = "https://api.github.com/gists"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    files = {}
    for fp in file_paths:
        name = os.path.basename(fp)
        with open(fp, "r", encoding="utf-8") as f:
            files[name] = {"content": f.read()}
    payload = {"description": GIST_DESCRIPTION, "public": True, "files": files}
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    if r.status_code >= 300:
        print(f"[WARN] Gist create failed {r.status_code}: {r.text[:200]}")
        return {}
    data = r.json()
    out = {}
    for name, meta in data.get("files", {}).items():
        raw = meta.get("raw_url")
        if raw:
            out[name] = raw
    print("[OK] Fichiers publiés sur Gist.")
    return out

def _repo_put_file(owner: str, repo: str, branch: str, path: str, content_b64: str, message: str) -> Optional[str]:
    """Create/update a file in repo and return raw URL."""
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    api_base = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"

    # check if exists to get sha
    sha = None
    rget = requests.get(api_base, headers=headers, params={"ref": branch}, timeout=30)
    if rget.status_code == 200:
        sha = rget.json().get("sha")

    payload = {"message": message, "content": content_b64, "branch": branch}
    if sha:
        payload["sha"] = sha

    r = requests.put(api_base, headers=headers, json=payload, timeout=30)
    if r.status_code not in (200,201):
        print(f"[WARN] Repo upload failed for {path} {r.status_code}: {r.text[:200]}")
        return None

    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
    return raw_url

def upload_files_to_repo(file_paths: List[str]) -> Dict[str, str]:
    """Return {filename: raw_url} via GitHub repo (branch)."""
    if not (GITHUB_TOKEN and GITHUB_REPO):
        print("[INFO] GITHUB_TOKEN ou GITHUB_REPO manquant; impossible d’uploader au repo.")
        return {}
    owner, repo = GITHUB_REPO.split("/", 1)
    out = {}
    for fp in file_paths:
        name = os.path.basename(fp)
        with open(fp, "rb") as f:
            content_b64 = base64.b64encode(f.read()).decode("utf-8")
        rel_path = f"{GITHUB_PATH_PREFIX}/{name}" if GITHUB_PATH_PREFIX else name
        raw = _repo_put_file(owner, repo, GITHUB_BRANCH, rel_path, content_b64, f"Upload {name} from scraper")
        if raw:
            out[name] = raw
    if out:
        print("[OK] Fichiers publiés sur GitHub repo (raw URLs).")
    return out

def upload_files_public(file_paths: List[str]) -> Dict[str, str]:
    if args.upload_target == "gist":
        return upload_files_to_gist(file_paths)
    elif args.upload_target == "repo":
        return upload_files_to_repo(file_paths)
    else:
        print("[INFO] --upload-target non fourni : pas d’upload public.")
        return {}

# --- Notion push (DB entries) ---

def push_to_notion(rows: List[Evidence]):
    if not args.notion:
        print("[INFO] --notion non fourni : pas d'insertion en base Notion.")
        return
    api_key = NOTION_API_KEY
    db_id = NOTION_DATABASE_ID
    if not (api_key and db_id):
        print("[INFO] Notion non configuré; skip.")
        return

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    url = "https://api.notion.com/v1/pages"

    def notion_page_payload(ev: Evidence) -> Dict:
        title_value = f"{ev.subreddit} – {ev.author}"
        return {
            "parent": {"database_id": db_id},
            "properties": {
                "Name": {"title": [{"text": {"content": title_value[:200]}}]},
                "Subreddit": {"rich_text": [{"text": {"content": f"r/{ev.subreddit}"}}]},
                "Thread URL": {"url": ev.thread_url},
                "Comment URL": {"url": ev.comment_url},
                "Author": {"rich_text": [{"text": {"content": ev.author}}]},
                "Score": {"number": ev.score},
                "Post": {"rich_text": [{"text": {"content": ev.post[:1900]}}]},
            }
        }

    for ev in rows:
        try:
            payload = notion_page_payload(ev)
            r = requests.post(url, headers=headers, json=payload, timeout=30)
            if r.status_code >= 300:
                print(f"[WARN] Notion push failed {r.status_code}: {r.text[:200]}")
            time.sleep(0.2)
        except Exception as e:
            print(f"[WARN] Notion error: {e}")
            time.sleep(0.5)

# --- Notion files (append MD/CSV to a block as external file links) ---

def append_file_urls_to_notion_block(urls_by_name: Dict[str, str]):
    if not args.notion_files:
        print("[INFO] --notion-files non fourni : pas d’ajout de fichiers sur Notion.")
        return
    if not NOTION_API_KEY:
        print("[INFO] NOTION_API_KEY manquant; skip --notion-files.")
        return
    if not NOTION_BLOCK_ID:
        print("[INFO] NOTION_BLOCK_ID manquant; mets-le dans .env.")
        return
    if not urls_by_name:
        print("[INFO] Aucune URL publique à ajouter (upload non effectué ?).")
        return

    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

    children = []
    for name, public_url in urls_by_name.items():
        children.append({
            "object": "block",
            "type": "file",
            "file": {
                "type": "external",
                "external": {"url": public_url},
                "caption": [{"type": "text", "text": {"content": name}}]
            }
        })

    url = f"https://api.notion.com/v1/blocks/{NOTION_BLOCK_ID}/children"
    payload = {"children": children}
    r = requests.patch(url, headers=headers, json=payload, timeout=30)
    if r.status_code >= 300:
        print(f"[WARN] Notion files append failed {r.status_code}: {r.text[:200]}")
    else:
        print("[OK] Fichiers ajoutés au bloc Notion comme liens externes.")

# --- Main ---

if __name__ == "__main__":
    t0 = time.time()

    rows = run_scrape()

    # dédup par comment_url
    uniq = {r.comment_url: r for r in rows}.values()
    uniq = list(uniq)

    # tri par score décroissant puis par subreddit/titre pour stabilité
    uniq.sort(key=lambda r: (-r.score, r.subreddit.lower(), r.thread_title.lower()))

    if uniq:
        export_csv(uniq, CSV_PATH)
        export_markdown(uniq, MD_PATH)

        # Upload public URLs (Gist/Repo) si demandé
        urls = upload_files_public([MD_PATH, CSV_PATH])

        # Push DB (si demandé)
        push_to_notion(uniq)

        # Append files to Notion block (si demandé) avec URLs publiques
        append_file_urls_to_notion_block(urls)
    else:
        print("[INFO] Aucun résultat pertinent trouvé (essaie d'autres requêtes/subreddits).")

    elapsed = time.time() - t0
    print(f"[DONE] Exécution terminée en {elapsed:.1f} s")
