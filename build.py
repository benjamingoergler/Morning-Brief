"""
Build script for the morning brief site.

Runs in GitHub Actions twice per day (08:30 and 15:00 CEST). Idempotent —
if a brief already exists for today, it's enriched with newly-published
articles (e.g. Morning Juice US that drops mid-morning).

Pipeline:
  1. Fetch FJ index page, extract URLs of today's mj_eu, mj_us and yesterday's wrap
  2. Fetch each article's full text
  3. Single Gemini API call → returns JSON with summaries + dockets per article
  4. Convert ET → CEST/CET using zoneinfo (handles DST)
  5. Write docs/archive/YYYY-MM-DD.json
  6. Update docs/manifest.json
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from google import genai
from google.genai import types


# ── Paths ──────────────────────────────────────────────────────────
ROOT     = Path(__file__).parent
DOCS     = ROOT / "docs"
ARCHIVE  = DOCS / "archive"
MANIFEST = DOCS / "manifest.json"

ARCHIVE.mkdir(parents=True, exist_ok=True)


# ── Config ─────────────────────────────────────────────────────────
FJ_INDEX = "https://features.financialjuice.com/"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

PARIS = ZoneInfo("Europe/Paris")
NEW_YORK = ZoneInfo("America/New_York")

GEMINI_MODEL = "gemini-2.5-flash"


# ── FJ scraping ────────────────────────────────────────────────────
def _slug_pattern(slug_re: str) -> re.Pattern:
    return re.compile(
        rf"https?://features\.financialjuice\.com/"
        rf"(?P<y>\d{{4}})/(?P<m>\d{{2}})/(?P<d>\d{{2}})/"
        rf"{slug_re}/?",
        re.IGNORECASE,
    )


def _find_latest(index_html: str, slug_re: str) -> tuple[str, date] | None:
    rx = _slug_pattern(slug_re)
    found = {}
    for m in rx.finditer(index_html):
        url = m.group(0).rstrip("/") + "/"
        found[url] = date(int(m["y"]), int(m["m"]), int(m["d"]))
    if not found:
        return None
    url = max(found, key=found.get)
    return url, found[url]


def _fetch_article_text_and_title(url: str) -> tuple[str, str]:
    r = requests.get(url, headers=BROWSER_HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    body = (
        soup.select_one("div.entry-content")
        or soup.select_one("article .post-content")
        or soup.find("article")
    )
    if not body:
        return "", ""
    for tag in body(["script", "style", "aside", "footer", "nav", "iframe"]):
        tag.decompose()
    text = body.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)

    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else ""
    return text, title


def fetch_fj_articles() -> dict:
    """Return {key: {url, date, title, text}} for mj_eu, wrap, mj_us.

    Each can be None if not found (e.g. mj_us not yet published in the morning).
    """
    print("→ Fetching FJ index…")
    r = requests.get(FJ_INDEX, headers=BROWSER_HEADERS, timeout=20)
    r.raise_for_status()
    idx = r.text

    today = date.today()

    out = {}
    for key, slug_re in [
        ("mj_eu", r"morning-juice-europe-session-prep[\w\-]*"),
        ("wrap",  r"[\w\-]+-us-market-wrap"),
        ("mj_us", r"morning-juice-us-session-prep[\w\-]*"),
    ]:
        match = _find_latest(idx, slug_re)
        if not match:
            print(f"  — {key}: not found in index")
            out[key] = None
            continue
        url, art_date = match

        # Sanity: keep mj_eu/mj_us only if today; wrap can be yesterday
        if key in ("mj_eu", "mj_us") and art_date != today:
            print(f"  — {key}: only stale article found ({art_date}), skipping")
            out[key] = None
            continue
        if key == "wrap" and (today - art_date).days > 4:
            print(f"  — {key}: too old ({art_date}), skipping")
            out[key] = None
            continue

        print(f"  ✓ {key}: {art_date} — fetching content…")
        text, title = _fetch_article_text_and_title(url)
        if not title:
            slug = url.rstrip("/").rsplit("/", 1)[-1]
            title = slug.replace("-", " ").title()

        out[key] = {
            "url":   url,
            "date":  art_date.isoformat(),
            "title": title,
            "text":  text,
        }

    return out


# ── Gemini summarisation ──────────────────────────────────────────
SUMMARY_PROMPT = """Tu es l'assistant d'un trader NQ futures basé en Suisse francophone. Voici 3 articles de Financial Juice (rédigés en anglais). Tu produis :

1. **summary** par article : un résumé en français préservant TOUT le niveau de détail de l'article original
2. **dockets** (Morning Juice uniquement) : events économiques listés dans la section "Docket"

RÈGLES POUR LE SUMMARY :
- Tu TRADUIS l'article en français — tu ne le résumes PAS ni ne le condenses
- Couvre TOUS les sujets abordés dans l'article : ne supprime rien, ne synthétise rien
- Si l'article a 8 paragraphes ou 10 thèmes, ton summary couvre les 8 paragraphes ou 10 thèmes
- Format en puces : chaque puce commence par "• " (point médiant + espace), une puce par sujet/paragraphe
- Garde TOUS les chiffres : niveaux d'indices, yields, prix matières, pourcentages, dates
- Garde les noms propres et acronymes en VO : Trump, Fed, OPEC, Brent, WTI, S&P 500, NDX, DXY, IFO, UMich, BoE, BoJ, ECB, etc.
- Reste strictement factuel — n'invente AUCUN chiffre, n'ajoute AUCUNE interprétation
- Pas d'intro, pas de conclusion, directement les puces

RÈGLES POUR LES DOCKETS :
- "time_et" = heure ET d'origine au format "HH:MM" sur 5 caractères, je convertirai côté serveur
- "cur" = code currency 3 lettres (GBP, EUR, USD, CAD, JPY, AUD, NZD, CHF...)
- "title" = titre de l'event en ANGLAIS, EXACTEMENT tel qu'écrit dans l'article (ne traduis pas, ne reformule pas — copie le titre original mot pour mot pour permettre le dédoublonnage entre articles EU et US)
- Si "forecast" ou "previous" sont vides, mets ""
- Si un article est marqué "ARTICLE PAS ENCORE PUBLIÉ", mets une chaîne vide pour son summary et un tableau vide pour ses dockets
"""

# Note : le texte des articles est concaténé après coup pour éviter
# les conflits entre str.format() et les accolades JSON du schéma.


def summarize_all(articles: dict) -> dict:
    """Single Gemini call for all 3 articles. Returns dict keyed by article key."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit("! GEMINI_API_KEY missing. Get a free key at https://aistudio.google.com/apikey")

    def text_of(key: str) -> str:
        a = articles.get(key)
        return a["text"][:10000] if a else "ARTICLE PAS ENCORE PUBLIÉ"

    prompt = (
        SUMMARY_PROMPT
        + "\n\nARTICLES :\n\n"
        + "=== MORNING JUICE EU ===\n" + text_of("mj_eu") + "\n\n"
        + "=== US MARKET WRAP ===\n"   + text_of("wrap")  + "\n\n"
        + "=== MORNING JUICE US ===\n" + text_of("mj_us") + "\n"
    )

    print(f"→ Calling Gemini ({GEMINI_MODEL})…")
    client = genai.Client(api_key=api_key)

    # Schéma JSON strict imposé à Gemini : la sortie est garantie de matcher,
    # ce qui élimine les ennuis de troncature mid-string et de format libre.
    docket_schema = {
        "type": "object",
        "properties": {
            "time_et":  {"type": "string"},
            "cur":      {"type": "string"},
            "title":    {"type": "string"},
            "forecast": {"type": "string"},
            "previous": {"type": "string"},
        },
        "required": ["time_et", "cur", "title", "forecast", "previous"],
    }
    article_schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "dockets": {"type": "array", "items": docket_schema},
        },
        "required": ["summary", "dockets"],
    }
    response_schema = {
        "type": "object",
        "properties": {
            "mj_eu": article_schema,
            "wrap":  article_schema,
            "mj_us": article_schema,
        },
        "required": ["mj_eu", "wrap", "mj_us"],
    }

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=response_schema,
        max_output_tokens=8000,
        temperature=0.3,
    )

    # Retry avec backoff exponentiel : 5s, 15s, 45s, 90s
    # Couvre les 503 UNAVAILABLE (saturation Google) et 429 RESOURCE_EXHAUSTED.
    # Si après 4 tentatives on échoue toujours, on fallback sur Flash-Lite
    # qui a une charge plus faible — qualité un peu moindre mais résultat utilisable.
    import time as _time
    MODELS_TO_TRY = [GEMINI_MODEL, "gemini-2.5-flash-lite"]
    DELAYS = [5, 15, 45, 90]
    last_err = None

    resp = None
    for model in MODELS_TO_TRY:
        for attempt, delay in enumerate([0] + DELAYS):
            if delay:
                print(f"  ⏳ retry in {delay}s (attempt {attempt}/{len(DELAYS)} on {model})…")
                _time.sleep(delay)
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=config,
                )
                if model != GEMINI_MODEL:
                    print(f"  ✓ succeeded on fallback model {model}")
                break
            except Exception as e:
                last_err = e
                msg = str(e)
                # Retry uniquement sur erreurs transitoires
                if "503" in msg or "UNAVAILABLE" in msg or "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                    continue
                # Autre erreur (auth, schéma, etc.) — pas la peine de retry
                raise
        if resp is not None:
            break

    if resp is None:
        print(f"  ! All retries exhausted, last error: {last_err}")
        raise last_err

    raw = resp.text.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  ! JSON parse error: {e}")
        print(f"  Response was:\n{raw[:2000]}")
        raise

    print("  ✓ done")
    return data


# ── ET → CEST conversion ──────────────────────────────────────────
def et_to_paris(time_et: str, ref_date: str) -> str:
    """Convert "HH:MM" ET on a given date to "HH:MM" Paris time."""
    try:
        d = date.fromisoformat(ref_date)
        dt_et = datetime.strptime(time_et, "%H:%M").replace(
            year=d.year, month=d.month, day=d.day, tzinfo=NEW_YORK
        )
        return dt_et.astimezone(PARIS).strftime("%H:%M")
    except Exception:
        return time_et  # fallback


def enrich_dockets(dockets: list[dict], ref_date: str) -> list[dict]:
    out = []
    for d in dockets:
        out.append({
            "time_et":  d.get("time_et", ""),
            "time_cet": et_to_paris(d.get("time_et", "00:00"), ref_date),
            "cur":      d.get("cur", ""),
            "title":    d.get("title", ""),
            "forecast": d.get("forecast", ""),
            "previous": d.get("previous", ""),
        })
    out.sort(key=lambda x: x["time_cet"])
    return out


# ── Persistence ───────────────────────────────────────────────────
def _norm_title(t: str) -> str:
    """Normalize titles for dedup: lowercase, collapse whitespace, strip punctuation noise."""
    import unicodedata
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w\s]", " ", t.lower())
    t = re.sub(r"\s+", " ", t).strip()
    return t


def merge_with_existing(today_iso: str, new_data: dict) -> dict:
    """Merge new data into existing archive file. New non-empty fields override."""
    target = ARCHIVE / f"{today_iso}.json"
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
    else:
        existing = {
            "date":    today_iso,
            "date_fr": "",
            "articles": {"mj_eu": None, "wrap": None, "mj_us": None},
            "dockets": [],
            "generated_at": "",
        }
    # Nettoyage compat : retire big_news des vieux fichiers s'il existait
    existing.pop("big_news", None)

    # Per-article merge: new wins if non-null
    for key in ("mj_eu", "wrap", "mj_us"):
        new_art = new_data["articles"].get(key)
        if new_art and new_art.get("summary"):
            existing["articles"][key] = new_art

    # Dockets : merge unique par (time_cet, normalized title), prefer fresh ones
    seen = {}
    for d in (new_data["dockets"] or []) + (existing["dockets"] or []):
        key = (d["time_cet"], _norm_title(d["title"]))
        if key not in seen:
            seen[key] = d
    existing["dockets"] = sorted(seen.values(), key=lambda x: x["time_cet"])

    existing["date"]    = today_iso
    existing["date_fr"] = new_data["date_fr"]
    existing["generated_at"] = datetime.now(PARIS).isoformat(timespec="seconds")
    return existing


def write_archive(today_iso: str, data: dict) -> None:
    target = ARCHIVE / f"{today_iso}.json"
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ {target.relative_to(ROOT)}")


def update_manifest(today_iso: str) -> None:
    files = sorted(ARCHIVE.glob("*.json"), reverse=True)
    days = []
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        wrap = (d.get("articles") or {}).get("wrap") or {}
        days.append({
            "date":     d.get("date", f.stem),
            "headline": wrap.get("title", "(brief sans US wrap)"),
        })
    manifest = {"today": today_iso, "days": days}
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ manifest.json ({len(days)} day(s))")


def date_fr(d: date) -> str:
    months = ["janvier", "février", "mars", "avril", "mai", "juin",
              "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
    days_fr = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    return f"{days_fr[d.weekday()]} {d.day} {months[d.month-1]} {d.year}"


# ── Main ──────────────────────────────────────────────────────────
def main():
    today = date.today()
    today_iso = today.isoformat()

    articles = fetch_fj_articles()
    summaries = summarize_all(articles)

    # Build per-article enriched data
    out_articles = {}
    for key in ("mj_eu", "wrap", "mj_us"):
        art = articles.get(key)
        sumdata = summaries.get(key) or {}
        if not art or not sumdata.get("summary"):
            out_articles[key] = None
            continue
        out_articles[key] = {
            "url":     art["url"],
            "date":    art["date"],
            "title":   art["title"],
            "summary": sumdata["summary"],
        }

    # Aggregate dockets from EU + US morning juice, convert to Paris time
    raw_dockets = []
    for key in ("mj_eu", "mj_us"):
        art = articles.get(key)
        sumdata = summaries.get(key) or {}
        ref_date = art["date"] if art else today_iso
        raw_dockets.extend(enrich_dockets(sumdata.get("dockets", []) or [], ref_date))

    # Dedupe across mj_eu/mj_us (some events may appear in both)
    seen = {}
    for d in raw_dockets:
        key = (d["time_cet"], _norm_title(d["title"]))
        if key not in seen:
            seen[key] = d
    final_dockets = sorted(seen.values(), key=lambda x: x["time_cet"])

    new_data = {
        "date":     today_iso,
        "date_fr":  date_fr(today),
        "articles": out_articles,
        "dockets":  final_dockets,
    }

    print("→ Writing archive…")
    merged = merge_with_existing(today_iso, new_data)
    write_archive(today_iso, merged)
    update_manifest(today_iso)

    summary_status = " · ".join(
        f"{k}={'✓' if merged['articles'].get(k) else '—'}"
        for k in ("mj_eu", "wrap", "mj_us")
    )
    print(f"\n✓ Brief built for {today_iso}  ({summary_status})  "
          f"· {len(merged['dockets'])} dockets")


if __name__ == "__main__":
    main()
