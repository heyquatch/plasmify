"""Check that every link in docs/wiki/*.html works, offline and on https://plasmify.github.io.

Looks at ``href`` and ``src`` attributes. Links between pages are relative, so the pages work
offline too; only 404.html uses root-absolute links, because the site serves it at any address.
A relative link must stay inside the wiki folder, since nothing outside it is published.
Links to the project on GitHub are allowed; one to a repository file must name a file that exists here.
Other external links (http:, https:, mailto: ...) are reported. For links with a ``#fragment``
into an HTML page, the target element id must exist as well.

Every page except 404.html must also carry its canonical address on the site, and sitemap.xml
must list exactly those addresses.

Usage: python3 scripts/check_wiki.py [wiki folder]   (standard library only)
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
WIKI = ROOT / "docs" / "wiki"
SITE = "https://plasmify.github.io/"
REPO = "https://github.com/heyquatch/plasmify/"
REPO_FILES = REPO + "blob/main/"


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[int, str, str]] = []  # (line, attribute, value)
        self.ids: set[str] = set()
        self.canonical: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "link" and ("rel", "canonical") in attrs:
            self.canonical = dict(attrs).get("href")
            return
        for name, value in attrs:
            if value is None:
                continue
            if name in ("id", "name") and (name == "id" or tag == "a"):
                self.ids.add(value)
            if name in ("href", "src"):
                self.links.append((self.getpos()[0], name, value))

    handle_startendtag = handle_starttag


def parse(path: Path) -> LinkParser:
    parser = LinkParser()
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    return parser


def shown(path: Path) -> Path:
    return path.relative_to(ROOT) if path.is_relative_to(ROOT) else path


def site_url(page: Path) -> str:
    """The address a page is linked at on the site."""
    return SITE if page.name == "index.html" else SITE + page.name


def check(wiki: Path) -> list[str]:
    pages = sorted(wiki.glob("*.html"))
    if not pages:
        return [f"{wiki}: no HTML pages found"]
    parsed = {page.resolve(): parse(page) for page in pages}
    problems = []
    for page, parser in parsed.items():
        if page.name != "404.html" and parser.canonical != site_url(page):
            problems.append(f"{shown(page)}: canonical link is {parser.canonical!r}, expected {site_url(page)!r}")
        for line, attribute, value in parser.links:
            where = f"{shown(page)}:{line}"
            if value.startswith(REPO):
                repo_file = urlsplit(value.removeprefix(REPO_FILES)).path if value.startswith(REPO_FILES) else ""
                if repo_file and not (ROOT / unquote(repo_file)).exists():
                    problems.append(f"{where}: {attribute}={value!r} points to a file missing from the repository")
                continue
            parts = urlsplit(value)
            if parts.scheme or parts.netloc or value.startswith("//"):
                problems.append(f"{where}: {attribute}={value!r} is not a relative link")
                continue
            if not parts.path:  # "#section" on the same page
                target = page
            else:
                base = wiki if parts.path.startswith("/") else page.parent
                target = (base / unquote(parts.path).lstrip("/")).resolve()
                if target.is_dir():
                    target = target / "index.html"
                if not target.is_relative_to(wiki):
                    problems.append(f"{where}: {attribute}={value!r} is outside the published wiki folder")
                    continue
                if not target.exists():
                    problems.append(f"{where}: {attribute}={value!r} points to a missing file")
                    continue
            if parts.fragment and target.suffix == ".html":
                ids = parsed[target].ids if target in parsed else parse(target).ids
                if unquote(parts.fragment) not in ids:
                    problems.append(f"{where}: {attribute}={value!r} points to a missing #{parts.fragment}")
    return problems + check_sitemap(wiki, pages)


def check_sitemap(wiki: Path, pages: list[Path]) -> list[str]:
    sitemap = wiki / "sitemap.xml"
    if not sitemap.exists():
        return [f"{shown(sitemap)}: missing"]
    listed = set(re.findall(r"<loc>(.*?)</loc>", sitemap.read_text(encoding="utf-8")))
    expected = {site_url(page) for page in pages if page.name != "404.html"}
    problems = [f"{shown(sitemap)}: {url} is missing" for url in sorted(expected - listed)]
    return problems + [f"{shown(sitemap)}: {url} is not a page" for url in sorted(listed - expected)]


def main(argv: list[str]) -> int:
    wiki = Path(argv[1]).resolve() if len(argv) > 1 else WIKI
    problems = check(wiki)
    for problem in problems:
        print(problem)
    pages = len(list(wiki.glob("*.html")))
    if problems:
        print(f"{len(problems)} problem(s) in {pages} page(s) and the sitemap")
        return 1
    print(f"All links in {pages} page(s) and the sitemap are fine")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
