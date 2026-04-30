"""
Extracts sections from HTML files and converts them to clean Markdown.

Processes all .html files in the input directory and writes .md files to the
output directory. With no --selectors flag, DEFAULT_SELECTORS is used and each
selector listed in SELECTOR_ALTERNATIVES is auto-swapped per file for the
variant that matches the most text — so layouts like xauusd (which uses
.layer-box > .left and .faq-section) work without a flag. Pass --selectors
to opt out and run exactly the selectors you supply.

Usage:
    python mdfier.py
    python mdfier.py --selectors ".info,.faq"
    python mdfier.py --input-dir html-input --output-dir md-output
    python mdfier.py --file html-input/page.html
"""

import argparse
import pathlib
import sys

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

_NESTED_INFO_PARENTS = (
    ".trade-card", ".trade-box", ".trade-mkt",
    ".example-box", ".forex-example", ".forex-pople",
    ".transparent-box",
)
_INFO_NOT = ", ".join(f"{p} .info" for p in _NESTED_INFO_PARENTS)

DEFAULT_SELECTORS = (
    f".info:not({_INFO_NOT})"   # page header / footer chrome; skip .info nested inside other components
    ",.content > .left"         # article body wrapper
    ",div.chart"
    # spec table at top level (gold.html). forex.html's spread table sits inside
    # `.transparent-box` and is rendered via that container's recursion, so we
    # exclude it here to avoid double-rendering.
    ",div.table:not(.transparent-box div.table)"
    ",div.trade-box"
    ",div.trade-mkt"            # forex.html: "Why Trade CFDs on Forex" cards
    ",div.example-box"
    ",div.forex-example"        # forex.html: trading example calculator
    ",div.forex-pople"          # forex.html: "How Forex Trading Works" intro
    ",div.transparent-box"      # forex.html: spreads table section
    ",div.trade-card"
    ",.faq"
)

# Per-file auto-detect: for each default selector below, try every candidate
# in the tuple and substitute the one matching the most text. Lets a single
# default cover layout variants (e.g. xauusd uses .layer-box > .left for the
# body and .faq-section for the FAQ; articles use .content > .left and .faq).
# The first entry in each tuple wins ties, so when both layouts are present
# the article-style selector keeps its existing behaviour.
SELECTOR_ALTERNATIVES = {
    # `div.html` is the Nuxt SSR wrapper used on oil-trading article pages
    # (iran-war, crude-oil-price-forecast) where the body sits at
    # `.content > div.html` instead of `.content > .left`. The :not() guard
    # excludes the per-FAQ-answer `div.html` Nuxt also emits inside
    # `.faq-item > .answer`, which would otherwise duplicate FAQ content.
    ".content > .left": (
        ".content > .left",
        ".layer-box > .left",
        "div.html:not(.answer div.html)",
    ),
    ".faq": (".faq", ".faq-section"),
}


def _join_inline(parts):
    """Join inline parts, ensuring spaces around markdown links."""
    result = []
    for part in parts:
        if not part:
            continue
        if result:
            prev = result[-1]
            needs_space = (
                (part.startswith("[") and prev and not prev[-1].isspace())
                or (prev.endswith(")") and part and not part[0].isspace()
                    and part[0] not in ".,;:!?)")
                or (part.startswith("**") and prev and prev[-1] not in " \t\n([")
                or (prev.endswith("**") and part and part[0] not in " \t\n.,;:!?)]")
            )
            if needs_space:
                result.append(" ")
        result.append(part)
    return "".join(result)


def convert_inline(element):
    """Convert an element's children to inline Markdown text."""
    if isinstance(element, Comment):
        return ""

    if isinstance(element, NavigableString):
        text = element.strip()
        return text if text else ""

    if not isinstance(element, Tag):
        return ""

    if element.name == "a":
        href = element.get("href", "")
        text = element.get_text(strip=True)
        if not href or href == "#":
            return ""
        if not (href.startswith("https://") or href.startswith("http://")):
            href = href.rstrip("/").rsplit("/", 1)[-1]
        if text:
            return f"[{text}]({href})"
        return text

    if element.name in ("strong", "b"):
        text = element.get_text(strip=True)
        return f"**{text}**" if text else ""

    if element.name in ("em", "i"):
        text = element.get_text(strip=True)
        return f"*{text}*" if text else ""

    if element.name == "br":
        return "\n"

    if element.name == "img":
        return ""

    # For spans and other inline elements, recurse into children
    parts = []
    for child in element.children:
        parts.append(convert_inline(child))
    return _join_inline(parts)


def convert_table(table):
    """Convert an HTML table to a Markdown pipe table."""
    rows = []
    for tr in table.find_all("tr"):
        cells = []
        for cell in tr.find_all(["th", "td"]):
            cells.append(cell.get_text(strip=True).replace("|", "\\|"))
        if cells:
            rows.append(cells)

    if not rows:
        return ""

    # Normalize column count
    max_cols = max(len(r) for r in rows)
    for r in rows:
        while len(r) < max_cols:
            r.append("")

    lines = []
    # First row as header
    lines.append("| " + " | ".join(rows[0]) + " |")
    lines.append("| " + " | ".join(["---"] * max_cols) + " |")
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines)


def convert_list(element):
    """Convert ol/ul to Markdown list."""
    lines = []
    for i, li in enumerate(element.find_all("li", recursive=False), 1):
        text = li.get_text(strip=True)
        if element.name == "ol":
            lines.append(f"{i}. {text}")
        else:
            lines.append(f"- {text}")
    return "\n".join(lines)


def convert_element(element, blocks):
    """Recursively convert an element to Markdown blocks."""
    if isinstance(element, (Comment, NavigableString)):
        return

    if not isinstance(element, Tag):
        return

    tag = element.name

    # Skip images
    if tag == "img":
        return

    # Skip UI chrome divs and mobile duplicates of components rendered above.
    # `eco-list-box` is the live-quote card grid inside `.trade-mkt` whose
    # static HTML only carries placeholder zeros for Bid/Ask/Spread.
    # `tabs-box` is product-category tab navigation.
    _skip_classes = {
        "btn", "share-box", "share",
        "card-list-mobile", "list-mobile", "table-mobile",
        "eco-list-box", "tabs-box",
    }
    classes_set = set(element.get("class", [])) if tag == "div" else set()
    if classes_set & _skip_classes:
        return
    # `.tips` is overloaded: chrome label (`.info > .tips` reading "Article") vs.
    # content (e.g. `.transparent-box > .tips`). Only skip the chrome variant.
    if "tips" in classes_set and "text" not in classes_set:
        parent_classes = set(element.parent.get("class", [])) if element.parent else set()
        if "info" in parent_classes:
            return

    # Headings
    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        level = int(tag[1])
        text = " ".join(element.get_text().split())
        if text:
            blocks.append("#" * level + " " + text)
        return

    # Paragraphs
    if tag == "p":
        parts = []
        for child in element.children:
            parts.append(convert_inline(child))
        text = _join_inline(parts).strip()
        if text:
            blocks.append(text)
        return

    # Tables
    if tag == "table":
        table_md = convert_table(element)
        if table_md:
            blocks.append(table_md)
        return

    # Lists
    if tag in ("ol", "ul"):
        list_md = convert_list(element)
        if list_md:
            blocks.append(list_md)
        return

    # Details/summary — render summary as bold, then recurse into body
    if tag == "details":
        summary = element.find("summary")
        if summary:
            summary_text = " ".join(summary.get_text().split())
            if summary_text:
                blocks.append(f"**{summary_text}**")
        for child in element.children:
            if isinstance(child, Tag) and child.name == "summary":
                continue
            convert_element(child, blocks)
        return

    if tag == "summary":
        return

    # Custom product-page div handlers
    classes = element.get("class", [])

    if tag == "div" and "table" in classes:
        # Two schemas in the corpus:
        #   gold.html spec table: .table-header (title) + .table-body > .row > [.cell.label, .cell.value]
        #   forex.html spread table: .table-header > .col (column names) + .table-body > .table-row > [.symbol, .bid, .ask, .actions]
        body = element.find(class_="table-body")
        header = element.find(class_="table-header")
        spread_rows = body.find_all(class_="table-row", recursive=False) if body else []
        if spread_rows:
            rows = []
            for tr in spread_rows:
                sym = tr.find(class_="symbol")
                bid = tr.find(class_="bid")
                ask = tr.find(class_="ask")
                if sym and bid and ask:
                    rows.append([
                        sym.get_text(strip=True),
                        bid.get_text(strip=True),
                        ask.get_text(strip=True),
                    ])
            if rows:
                lines = ["| Pair | Bid | Ask |", "| --- | --- | --- |"]
                for r in rows:
                    lines.append("| " + " | ".join(r) + " |")
                blocks.append("\n".join(lines))
            return
        # Property/value spec table.
        if header:
            header_text = header.get_text(strip=True)
            if header_text:
                blocks.append(f"**{header_text}**")
        prop_rows = element.find_all(class_="row")
        if prop_rows:
            lines = ["| Property | Value |", "| --- | --- |"]
            for row in prop_rows:
                cells = row.find_all(class_="cell")
                if len(cells) >= 2:
                    lines.append(f"| {cells[0].get_text(strip=True)} | {cells[1].get_text(strip=True)} |")
            blocks.append("\n".join(lines))
        return

    if tag == "div" and "chart" in classes:
        name_span = element.find(class_="name")
        top_sub = element.find(class_="top-sub")
        if name_span:
            heading = name_span.get_text(strip=True)
            if top_sub:
                heading += f" — {top_sub.get_text(strip=True)}"
            blocks.append(f"## {heading}")
        for table_div in element.find_all("div", class_="table"):
            convert_element(table_div, blocks)
        return

    if tag == "div" and "trade-box" in classes:
        h2 = element.find("h2", class_="title")
        if h2:
            blocks.append(f"## {h2.get_text(strip=True)}")
        text_div = element.find("div", class_="text")
        if text_div:
            blocks.append(text_div.get_text(strip=True))
        desktop_list = element.find("div", class_="list-desktop")
        if desktop_list:
            for item in desktop_list.find_all("div", class_="item", recursive=False):
                l_title = item.find(class_="l-title")
                l_text = item.find(class_="l-text")
                t = l_title.get_text(strip=True) if l_title else ""
                b = l_text.get_text(strip=True) if l_text else ""
                if t and b:
                    blocks.append(f"**{t}** — {b}")
                elif t:
                    blocks.append(f"**{t}**")
        return

    if tag == "div" and "forex-example" in classes:
        h2 = element.find("h2", class_="title")
        if h2:
            blocks.append(f"## {' '.join(h2.get_text().split())}")
        calc_title = element.find(class_="calc-title")
        if calc_title:
            blocks.append(f"### {calc_title.get_text(strip=True)}")
            for row in element.find_all(class_="calc-row"):
                label = row.find(class_="label")
                value = row.find(class_="value")
                if label and value:
                    val_text = _join_inline([convert_inline(c) for c in value.children]).strip()
                    blocks.append(f"- **{label.get_text(strip=True)}**: {val_text}")
        for title in element.find_all(class_="pos-title"):
            blocks.append(f"### {title.get_text(strip=True)}")
            text = title.parent.find(class_="pos-text")
            if text:
                parts = [convert_inline(c) for c in text.children]
                t = _join_inline(parts).strip()
                if t:
                    blocks.append(t)
        return

    if tag == "div" and "example-box" in classes:
        h2 = element.find(class_="example-title")
        if h2:
            blocks.append(f"## {' '.join(h2.get_text().split())}")
        for card in element.find_all("div", class_="card"):
            card_title = card.find(class_="card-title")
            if card_title:
                blocks.append(f"### {card_title.get_text(strip=True)}")
            for row in card.find_all(class_="card-row"):
                label = row.find(class_="row-label")
                value = row.find(class_="row-value")
                if label and value:
                    val_text = _join_inline([convert_inline(c) for c in value.children]).strip()
                    blocks.append(f"- **{label.get_text(strip=True)}**: {val_text}")
            card_text = card.find(class_="card-text")
            if card_text:
                parts = [convert_inline(c) for c in card_text.children]
                text = _join_inline(parts).strip()
                if text:
                    blocks.append(text)
        return

    if tag == "div" and "trade-card" in classes:
        h2 = element.find("h2", class_="title")
        if h2:
            blocks.append(f"## {' '.join(h2.get_text().split())}")
        for layer in element.find_all("div", class_="item-layer"):
            front = layer.find("div", class_="front")
            back = layer.find("div", class_="back")
            name_text = ""
            if front:
                name = front.find(class_="name")
                if name:
                    name_text = " ".join(name.get_text().split())
            desc_text = ""
            if back:
                desc = back.find(class_="description")
                if desc:
                    desc_text = " ".join(desc.get_text().split())
            if name_text and desc_text:
                blocks.append(f"- **{name_text}** — {desc_text}")
            elif name_text:
                blocks.append(f"- {name_text}")
        return

    # Check if this div is a leaf (no block-level children)
    block_tags = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "table", "ol", "ul", "div"}
    has_block_child = any(
        isinstance(c, Tag) and c.name in block_tags for c in element.children
    )
    if not has_block_child:
        # Treat as a text block — extract inline content
        parts = []
        for child in element.children:
            parts.append(convert_inline(child))
        text = _join_inline(parts).strip()
        # Skip trivial UI text (icons, single chars, short labels)
        if text and len(text) > 3:
            blocks.append(text)
        return

    # For divs and other containers, recurse into children
    for child in element.children:
        convert_element(child, blocks)


def unwrap_chrome_source_view(soup):
    """If the file is a Chrome View Source wrapper, extract and re-parse the real HTML."""
    cells = soup.find_all("td", class_="line-content")
    if not cells:
        return soup
    lines = [cell.get_text() for cell in cells]
    real_html = "\n".join(lines)
    return BeautifulSoup(real_html, "html.parser")


def convert_section(element):
    """Convert a top-level section element to Markdown text."""
    blocks = []
    convert_element(element, blocks)
    return "\n\n".join(blocks)


def _split_selectors(s):
    """Split a comma-separated selector string, respecting commas inside ()/[]."""
    out = []
    depth = 0
    buf = []
    for ch in s:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            sel = "".join(buf).strip()
            if sel:
                out.append(sel)
            buf = []
        else:
            buf.append(ch)
    sel = "".join(buf).strip()
    if sel:
        out.append(sel)
    return out


def pick_alternative(selector, soup):
    """For a default selector with known alternatives, return the variant whose
    matches contain the most text on this page. Falls through unchanged when
    the selector has no alternatives."""
    alternatives = SELECTOR_ALTERNATIVES.get(selector)
    if not alternatives:
        return selector
    best = selector
    best_len = -1
    for cand in alternatives:
        total = sum(len(el.get_text(strip=True)) for el in soup.select(cand))
        if total > best_len:
            best_len = total
            best = cand
    return best


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract HTML sections and convert to Markdown"
    )
    parser.add_argument(
        "--selectors",
        default=None,
        help=f"Comma-separated CSS selectors. When omitted, defaults to "
             f"'{DEFAULT_SELECTORS}' with per-file auto-detection of "
             f"layout variants (see SELECTOR_ALTERNATIVES).",
    )
    parser.add_argument(
        "--input-dir",
        default="html-input",
        help="Directory containing .html files (default: html-input)",
    )
    parser.add_argument(
        "--output-dir",
        default="md-output",
        help="Directory to write .md files (default: md-output)",
    )
    parser.add_argument(
        "--file",
        help="Convert a single .html file instead of the input directory",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    output_dir = pathlib.Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    auto_alternates = args.selectors is None
    selector_str = args.selectors if args.selectors is not None else DEFAULT_SELECTORS
    selectors = _split_selectors(selector_str)

    if args.file:
        file_path = pathlib.Path(args.file)
        if not file_path.is_file():
            print(f"File not found: {file_path}", file=sys.stderr)
            return
        html_files = [file_path]
    else:
        input_dir = pathlib.Path(args.input_dir)
        html_files = sorted(input_dir.glob("*.html"))
        if not html_files:
            print(f"No .html files found in {input_dir}", file=sys.stderr)
            return

    for html_path in html_files:
        output_path = output_dir / (html_path.stem + ".md")
        try:
            with open(html_path, encoding="utf-8") as f:
                soup = BeautifulSoup(f.read(), "html.parser")

            soup = unwrap_chrome_source_view(soup)

            title_tag = soup.find("title")
            title = title_tag.get_text(strip=True) if title_tag else ""

            file_selectors = list(selectors)
            if auto_alternates:
                file_selectors = [
                    pick_alternative(s, soup) for s in file_selectors
                ]

            sections = []
            for selector in file_selectors:
                for element in soup.select(selector):
                    md = convert_section(element)
                    if md:
                        sections.append(md)

            body = "\n\n---\n\n".join(sections) + "\n"
            output = f"---\ntitle: {title}\n---\n\n" + body if title else body

            with open(output_path, "w", encoding="utf-8") as f:
                f.write(output)

            print(f"{html_path.name} → {output_path.name}")
        except Exception as e:
            print(f"Warning: skipping {html_path.name}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
