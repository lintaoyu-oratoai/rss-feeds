"""Generate RSS feed for the Speechmatics Articles & News
(https://www.speechmatics.com/company/articles-and-news).

This site exposes real published dates via article:published_time meta tags
and JSON-LD, so we don't need cache-based first-seen fallbacks (but we still
cache to preserve historical posts that drop off the index page).
"""

import argparse
import re
from datetime import datetime
from urllib.parse import urljoin

import pytz
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator

from utils import (
    deserialize_entries,
    fetch_page,
    load_cache,
    merge_entries,
    save_cache,
    save_rss_feed,
    setup_feed_links,
    setup_logging,
    sort_posts_for_feed,
    stable_fallback_date,
)

logger = setup_logging()

FEED_NAME = "speechmatics"
BLOG_URL = "https://www.speechmatics.com/company/articles-and-news"
POST_PREFIX = "/company/articles-and-news/"
FEED_TITLE = "Speechmatics Articles & News"
FEED_DESCRIPTION = "Speech-to-text, voice AI, and product news from Speechmatics"


def discover_posts(blog_url: str) -> list[dict]:
    html = fetch_page(blog_url)
    soup = BeautifulSoup(html, "html.parser")
    posts: list[dict] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith(POST_PREFIX) or href.rstrip("/") == POST_PREFIX.rstrip("/"):
            continue
        link = urljoin(blog_url, href).split("#", 1)[0].split("?", 1)[0]
        if link in seen:
            continue
        seen.add(link)
        title = a.get_text(" ", strip=True) or a.get("aria-label", "") or link
        posts.append({"title": title[:300], "link": link})
    logger.info(f"Discovered {len(posts)} post links on {blog_url}")
    return posts


def enrich_from_post_page(post: dict) -> dict:
    try:
        html = fetch_page(post["link"])
    except Exception as e:
        logger.warning(f"Could not enrich {post['link']}: {e}")
        return post
    m = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]+)"', html)
    if m and len(m.group(1).strip()) > 5:
        post["title"] = m.group(1).strip()
    m = re.search(r'<meta[^>]*property="og:description"[^>]*content="([^"]+)"', html)
    if m:
        post["description"] = m.group(1).strip()
    for pattern in (
        r'<meta[^>]*property="article:published_time"[^>]*content="([^"]+)"',
        r'"datePublished"\s*:\s*"([^"]+)"',
        r'<time[^>]*datetime="([^"]+)"',
    ):
        m = re.search(pattern, html)
        if m:
            try:
                d = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
                if d.tzinfo is None:
                    d = d.replace(tzinfo=pytz.UTC)
                post["date"] = d
                break
            except ValueError:
                continue
    return post


def main(full_reset: bool = False) -> bool:
    cache = load_cache(FEED_NAME)
    cached_entries = [] if full_reset else deserialize_entries(cache.get("entries", []))
    cached_links = {e["link"] for e in cached_entries}

    discovered = discover_posts(BLOG_URL)
    if not discovered:
        logger.warning("No posts discovered.")
        return False

    new_posts: list[dict] = []
    for p in discovered:
        if p["link"] in cached_links:
            continue
        p = enrich_from_post_page(p)
        p.setdefault("description", p["title"])
        if "date" not in p:
            p["date"] = stable_fallback_date(p["link"])
            logger.info(f"Using fallback date for {p['link']}")
        p.setdefault("category", "Articles & News")
        new_posts.append(p)

    logger.info(f"{len(new_posts)} new posts (cache had {len(cached_entries)})")

    merged = merge_entries(new_posts, cached_entries) if cached_entries else sort_posts_for_feed(new_posts)
    save_cache(FEED_NAME, merged)

    fg = FeedGenerator()
    fg.title(FEED_TITLE)
    fg.description(FEED_DESCRIPTION)
    fg.language("en")
    fg.author({"name": "Speechmatics"})
    setup_feed_links(fg, BLOG_URL, FEED_NAME)
    for post in merged:
        fe = fg.add_entry()
        fe.title(post["title"])
        fe.description(post["description"])
        fe.link(href=post["link"])
        fe.id(post["link"])
        fe.category(term=post.get("category", "Articles & News"))
        if post.get("date"):
            fe.published(post["date"])
    save_rss_feed(fg, FEED_NAME)
    logger.info("Done!")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Speechmatics Articles & News RSS feed")
    parser.add_argument("--full", action="store_true", help="Force full reset")
    args = parser.parse_args()
    main(full_reset=args.full)
