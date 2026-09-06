import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag

from config import GUIDES_BASE_URL, GUIDES_DOCS_URL, SCRAPED_DIR

_BOILERPLATE = re.compile(
    r"Edit on GitHub|Written by|Edited by|breadcrumb|Server Plugins\s*$",
    re.IGNORECASE,
)

_CONTENT_TAGS = frozenset([
    "h1", "h2", "h3", "h4",
    "p", "pre", "ul", "ol", "table", "blockquote",
    "dl", "details",
])


def scrape_guides() -> list[dict]:
    SCRAPED_DIR.mkdir(parents=True, exist_ok=True)

    visited = set()
    to_visit = [GUIDES_DOCS_URL]
    pages = []

    with httpx.Client(
        follow_redirects=True,
        timeout=30,
        headers={"User-Agent": "HytaleDocsMCP/1.0 (documentation indexer)"},
    ) as client:
        while to_visit:
            url = to_visit.pop(0)
            normalized = url.rstrip("/")
            if normalized in visited:
                continue
            visited.add(normalized)

            try:
                resp = client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError:
                continue

            soup = BeautifulSoup(resp.text, "lxml")

            for a in soup.find_all("a", href=True):
                href = a["href"]
                full_url = urljoin(url, href)
                parsed = urlparse(full_url)
                clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                if (
                    clean.rstrip("/") not in visited
                    and parsed.netloc == urlparse(GUIDES_BASE_URL).netloc
                    and "/en/docs" in parsed.path
                    and clean not in to_visit
                ):
                    to_visit.append(clean)

            page_data = _extract_page(soup, url)
            if page_data and page_data["content"].strip():
                pages.append(page_data)

    for local_page in _parse_local_guides():
        pages.append(local_page)

    output = SCRAPED_DIR / "guides.json"
    output.write_text(json.dumps(pages, indent=2, ensure_ascii=False), encoding="utf-8")
    return pages


def _render_element(el: Tag) -> str:
    """Render a single content element to readable markdown-ish text."""
    name = el.name

    if name == "pre":
        code = el.find("code")
        raw = code.get_text() if code else el.get_text()
        lang = ""
        if code:
            cls = code.get("class", [])
            for c in cls:
                if isinstance(c, str) and c.startswith("language-"):
                    lang = c[len("language-"):]
                    break
        return f"```{lang}\n{raw}\n```"

    if name in ("ul", "ol"):
        items = []
        for i, li in enumerate(el.find_all("li", recursive=False)):
            nested_pre = li.find("pre")
            if nested_pre:
                li_text = li.get_text(separator=" ", strip=True)
                pre_text = _render_element(nested_pre)
                inline = li_text.replace(nested_pre.get_text(strip=True), "").strip()
                bullet = f"{i+1}." if name == "ol" else "-"
                entry = f"{bullet} {inline}\n{pre_text}" if inline else f"{bullet}\n{pre_text}"
            else:
                bullet = f"{i+1}." if name == "ol" else "-"
                entry = f"{bullet} {li.get_text(separator=' ', strip=True)}"
            items.append(entry)
        return "\n".join(items)

    if name == "table":
        rows = []
        for tr in el.find_all("tr"):
            cells = [td.get_text(separator=" ", strip=True) for td in tr.find_all(["th", "td"])]
            rows.append("| " + " | ".join(cells) + " |")
            if tr.find("th") and len(rows) == 1:
                rows.append("| " + " | ".join("---" for _ in cells) + " |")
        return "\n".join(rows)

    if name == "blockquote":
        text = el.get_text(separator=" ", strip=True)
        return "> " + text.replace("\n", "\n> ")

    if name == "dl":
        parts = []
        for child in el.children:
            if isinstance(child, Tag):
                if child.name == "dt":
                    parts.append(f"**{child.get_text(separator=' ', strip=True)}**")
                elif child.name == "dd":
                    parts.append(f"  {child.get_text(separator=' ', strip=True)}")
        return "\n".join(parts)

    if name == "details":
        summary = el.find("summary")
        summary_text = summary.get_text(separator=" ", strip=True) if summary else "Details"
        body_parts = []
        for child in el.children:
            if isinstance(child, Tag) and child.name != "summary":
                if child.name in _CONTENT_TAGS:
                    body_parts.append(_render_element(child))
                else:
                    t = child.get_text(separator=" ", strip=True)
                    if t:
                        body_parts.append(t)
        return f"**{summary_text}**\n" + "\n".join(body_parts)

    return el.get_text(separator=" ", strip=True)


def _extract_page(soup: BeautifulSoup, url: str) -> dict | None:
    title_el = soup.find("h1") or soup.find("title")
    title = title_el.get_text(strip=True) if title_el else url.split("/")[-1]

    main = (
        soup.find("main")
        or soup.find("article")
        or soup.find("div", class_=re.compile(r"content|docs|markdown", re.I))
    )
    if not main:
        return None

    for tag in main.find_all(["nav", "footer", "header", "aside", "script", "style"]):
        tag.decompose()

    elements = main.find_all(_CONTENT_TAGS)

    seen = set()
    sections = []
    current_heading = title
    current_text = []

    for el in elements:
        el_id = id(el)
        if el_id in seen:
            continue
        seen.add(el_id)

        if _is_nested_in_seen(el, seen):
            continue

        if el.name in ("h1", "h2", "h3", "h4"):
            if current_text:
                combined = "\n\n".join(current_text).strip()
                if combined:
                    sections.append({
                        "heading": current_heading,
                        "text": combined,
                    })
            current_heading = el.get_text(separator=" ", strip=True)
            current_text = []
        else:
            rendered = _render_element(el)
            if rendered and not _BOILERPLATE.search(rendered):
                current_text.append(rendered)

    if current_text:
        combined = "\n\n".join(current_text).strip()
        if combined:
            sections.append({
                "heading": current_heading,
                "text": combined,
            })

    full_content = "\n\n".join(
        f"## {s['heading']}\n{s['text']}" for s in sections
    )

    return {
        "url": url,
        "title": title,
        "sections": sections,
        "content": full_content,
    }


def _is_nested_in_seen(el: Tag, seen: set) -> bool:
    """Check if el is nested inside another element we already processed."""
    parent = el.parent
    while parent:
        if id(parent) in seen:
            return True
        parent = parent.parent
    return False


_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)


def _parse_local_guides() -> list[dict]:
    """Parse local .md files in SCRAPED_DIR into the same page format as web guides."""
    pages = []
    for md_file in sorted(SCRAPED_DIR.glob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        if not text.strip():
            continue

        title = md_file.stem.replace("_", " ").title()
        first_line = text.lstrip().split("\n", 1)[0]
        if first_line.startswith("# "):
            title = first_line[2:].strip()

        sections = []
        current_heading = title
        current_lines: list[str] = []

        for line in text.split("\n"):
            m = _HEADING_RE.match(line)
            if m:
                if current_lines:
                    combined = "\n".join(current_lines).strip()
                    if combined:
                        sections.append({"heading": current_heading, "text": combined})
                current_heading = m.group(2).strip()
                current_lines = []
            else:
                current_lines.append(line)

        if current_lines:
            combined = "\n".join(current_lines).strip()
            if combined:
                sections.append({"heading": current_heading, "text": combined})

        if not sections:
            continue

        full_content = "\n\n".join(f"## {s['heading']}\n{s['text']}" for s in sections)
        pages.append({
            "url": f"local://{md_file.name}",
            "title": title,
            "sections": sections,
            "content": full_content,
        })
    return pages
