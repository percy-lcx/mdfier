"""
Fetch one or more URLs and save their HTML to disk.

Usage:
    python fetch_html.py https://example.com/page
    python fetch_html.py https://a.com/x https://b.com/y
    python fetch_html.py --file urls.txt
    python fetch_html.py --file urls.txt --output-dir html-input/gold
"""

import argparse
import pathlib
import re
import sys
import urllib.parse
import urllib.request

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


def slug_from_url(url):
    """Derive a safe filename stem from a URL's last path segment."""
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.rstrip("/")
    stem = path.rsplit("/", 1)[-1] if path else ""
    if stem.lower().endswith(".html"):
        stem = stem[:-5]
    if not stem:
        stem = parsed.netloc.replace(":", "_") or "page"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-.") or "page"
    return stem


def unique_path(directory, stem, suffix=".html"):
    """Return a path in `directory` that doesn't already exist."""
    path = directory / f"{stem}{suffix}"
    i = 2
    while path.exists():
        path = directory / f"{stem}-{i}{suffix}"
        i += 1
    return path


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def read_url_list(path):
    urls = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            urls.append(line)
    return urls


def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch URL(s) and save the HTML to disk."
    )
    parser.add_argument("urls", nargs="*", help="One or more URLs to fetch")
    parser.add_argument(
        "--file",
        help="Path to a text file with one URL per line (blank lines and # comments ignored)",
    )
    parser.add_argument(
        "--output-dir",
        default="html-input",
        help="Directory to write .html files (default: html-input)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Per-request timeout in seconds (default: 30)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    urls = list(args.urls)
    if args.file:
        urls.extend(read_url_list(args.file))

    if not urls:
        print("No URLs provided. Pass URLs as arguments or use --file.", file=sys.stderr)
        sys.exit(2)

    output_dir = pathlib.Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    failures = 0
    for url in urls:
        try:
            html = fetch(url, timeout=args.timeout)
            stem = slug_from_url(url)
            out_path = unique_path(output_dir, stem)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"{url} → {out_path}")
        except Exception as e:
            failures += 1
            print(f"Warning: failed to fetch {url}: {e}", file=sys.stderr)

    if failures and failures == len(urls):
        sys.exit(1)


if __name__ == "__main__":
    main()
