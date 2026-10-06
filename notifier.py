"""ReleaseRadar: poll an RSS feed and post new releases to a Discord webhook."""

import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import feedparser
import requests
from bs4 import BeautifulSoup

FEED_URL = os.environ.get("FEED_URL") or "https://atlas.rssly.org/feed"
STATE_PATH = Path(__file__).parent / "data" / "seen_items.json"
MAX_SEEN = 500
EMBED_COLOR = 0x2ECC71
HTTP_TIMEOUT = 15
SYNOPSIS_LIMIT = 1000

log = logging.getLogger("releaseradar")

FIELD_PATTERNS = {
    "imdb": re.compile(r"IMDB Rating:\s*([^\n<]+)", re.IGNORECASE),
    "genre": re.compile(r"Genre:\s*([^\n<]+)", re.IGNORECASE),
    "size": re.compile(r"Size:\s*([^\n<]+)", re.IGNORECASE),
    "runtime": re.compile(r"Runtime:\s*([^\n<]+)", re.IGNORECASE),
}
LABEL_LINE = re.compile(r"^(IMDB Rating|Genre|Size|Runtime)\s*:", re.IGNORECASE)
URL_PATTERN = re.compile(r"https?://\S+")


def parse_description(html):
    """Extract poster, IMDb, genre, size, runtime and synopsis from feed HTML."""
    result = {
        "poster": None,
        "imdb": None,
        "genre": None,
        "size": None,
        "runtime": None,
        "synopsis": "",
    }
    if not html:
        return result

    soup = BeautifulSoup(html, "html.parser")

    img = soup.find("img", src=True)
    if img:
        result["poster"] = img["src"].strip()

    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(["img", "a"]):
        # Keep link text (rarely useful) out of the text blob; URLs are dropped.
        tag.decompose()
    text = soup.get_text()

    for key, pattern in FIELD_PATTERNS.items():
        match = pattern.search(text)
        if match:
            result[key] = match.group(1).strip() or None

    synopsis_lines = []
    for line in text.splitlines():
        line = URL_PATTERN.sub("", line).strip()
        if line and not LABEL_LINE.match(line):
            synopsis_lines.append(line)
    result["synopsis"] = " ".join(synopsis_lines)
    return result


def movie_key(entry):
    """Group qualities of the same movie: the link without its #fragment."""
    link = entry.get("link") or ""
    return link.split("#")[0] or entry.get("title", "")


def split_title(title):
    """Split 'Name (2026) [1080p] [WEBRip]' into ('Name (2026)', '1080p WEBRip')."""
    name = re.split(r"\s*\[", title, maxsplit=1)[0].strip() or title
    tags = [t for t in re.findall(r"\[([^\]]+)\]", title) if "YTS" not in t.upper()]
    return name, " ".join(tags)


def build_movie_embed(items):
    """One embed per movie. items is a list of (entry, meta) for its releases."""
    entry, meta = items[0]
    name, _ = split_title(entry.get("title", "Untitled release"))
    lines = []
    for e, m in items:
        _, label = split_title(e.get("title", ""))
        label = label or "Release"
        text = f"[{label}]({e['link']})" if e.get("link") else label
        if m["size"]:
            text += f" ({m['size']})"
        lines.append(text)

    fields = []
    if meta["imdb"]:
        fields.append({"name": "⭐ IMDb", "value": meta["imdb"], "inline": True})
    if meta["runtime"]:
        fields.append({"name": "⏱️ Runtime", "value": meta["runtime"], "inline": True})
    if meta["genre"]:
        fields.append({"name": "🎭 Genre", "value": meta["genre"], "inline": False})
    fields.append({"name": "📥 Releases", "value": "\n".join(lines)[:1024], "inline": False})

    embed = {"title": name[:256], "color": EMBED_COLOR, "fields": fields}
    if entry.get("link"):
        embed["url"] = entry["link"].split("#")[0]
    if meta["synopsis"]:
        embed["description"] = meta["synopsis"][:SYNOPSIS_LIMIT]
    if meta["poster"]:
        embed["thumbnail"] = {"url": meta["poster"]}
    newest = items[-1][0]
    if newest.get("published"):
        embed["footer"] = {"text": f"Published: {newest['published']}"}
    return embed


def load_seen(path=STATE_PATH):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        log.error("State file is not valid JSON; refusing to continue.")
        raise SystemExit(1)
    return data if isinstance(data, list) else []


def save_seen(seen, path=STATE_PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    trimmed = seen[-MAX_SEEN:]
    Path(path).write_text(json.dumps(trimmed, indent=2) + "\n", encoding="utf-8")


def send_to_discord(webhook_url, embeds, mention=True):
    """POST one message with embeds. Returns True on 200/204. Never logs the webhook URL."""
    payload = {"embeds": embeds}
    if mention:
        payload["content"] = "@everyone"
        payload["allowed_mentions"] = {"parse": ["everyone"]}
    for attempt in range(2):
        try:
            resp = requests.post(
                webhook_url,
                json=payload,
                timeout=HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            # Exception text can contain the URL, so log only the class name.
            log.error("Discord request failed: %s", type(exc).__name__)
            return False
        if resp.status_code in (200, 204):
            return True
        if resp.status_code == 429 and attempt == 0:
            try:
                delay = min(float(resp.json().get("retry_after", 1)), 10)
            except (ValueError, AttributeError):
                delay = 1
            time.sleep(delay)
            continue
        log.error("Discord returned HTTP %s", resp.status_code)
        return False
    return False


def entry_key(entry):
    return entry.get("id") or entry.get("link")


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    dry_run = not webhook_url
    if dry_run:
        log.info("DISCORD_WEBHOOK_URL not set; running in dry-run mode.")

    feed = feedparser.parse(FEED_URL)
    if feed.bozo and not feed.entries:
        log.error("Could not fetch or parse feed (%s).", type(feed.bozo_exception).__name__)
        return 1

    seen = load_seen()
    seen_set = set(seen)
    new_entries = [e for e in feed.entries if entry_key(e) and entry_key(e) not in seen_set]
    new_entries.reverse()  # feeds are newest first; send oldest first
    log.info("%d entries in feed, %d new.", len(feed.entries), len(new_entries))

    grouped = {}
    for entry in new_entries:  # dicts keep insertion order, so oldest movie first
        meta = parse_description(entry.get("summary") or entry.get("description"))
        grouped.setdefault(movie_key(entry), []).append((entry, meta))
    groups = [
        (build_movie_embed(items), [entry_key(e) for e, _ in items])
        for items in grouped.values()
    ]

    if dry_run:
        for embed, _ in groups:
            log.info("[dry-run] %s", json.dumps(embed, ensure_ascii=False))
        return 0

    sent = 0
    pinged = False
    for embed, keys in groups:
        # Only the first delivered message of a run pings @everyone.
        if send_to_discord(webhook_url, [embed], mention=not pinged):
            pinged = True
            seen.extend(keys)
            sent += len(keys)
            time.sleep(0.5)  # stay clear of webhook rate limits
        else:
            log.warning("Message failed; its items will be retried next run.")

    if sent:
        save_seen(seen)
    log.info("Marked %d release(s) as sent.", sent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
