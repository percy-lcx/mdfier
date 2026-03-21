"""
Extracts sections from HTML files and converts them to clean Markdown.

Processes all .html files in the input directory and writes .md files to the output directory.

Usage:
    python mdfier.py
    python mdfier.py --selectors ".info h1,.content > .left,.faq"
    python mdfier.py --input-dir html-input --output-dir md-output
"""

import argparse
import pathlib
import sys

from bs4 import BeautifulSoup, NavigableString, Tag

DEFAULT_SELECTORS = ".info,.content > .left,.faq,div.chart,div.trade-box,div.example-box,div.trade-card"


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
    if isinstance(element, NavigableString):
        return

    if not isinstance(element, Tag):
        return

    tag = element.name

    # Skip images
    if tag == "img":
        return

    # Skip UI chrome divs
    _skip_classes = {"btn", "tips", "share-box", "share"}
    if tag == "div" and _skip_classes & set(element.get("class", [])):
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

    # Custom product-page div handlers
    classes = element.get("class", [])

    if tag == "div" and "table" in classes:
        header = element.find(class_="table-header")
        if header:
            blocks.append(f"**{header.get_text(strip=True)}**")
        rows = element.find_all(class_="row")
        if rows:
            lines = ["| Property | Value |", "| --- | --- |"]
            for row in rows:
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
            if front:
                name = front.find(class_="name")
                if name:
                    blocks.append(f"- {' '.join(name.get_text().split())}")
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


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract HTML sections and convert to Markdown"
    )
    parser.add_argument(
        "--selectors",
        default=DEFAULT_SELECTORS,
        help=f"Comma-separated CSS selectors (default: {DEFAULT_SELECTORS})",
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
    return parser.parse_args()


def main():
    args = parse_args()

    input_dir = pathlib.Path(args.input_dir)
    output_dir = pathlib.Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    selectors = [s.strip() for s in args.selectors.split(",")]

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

            sections = []
            for selector in selectors:
                element = soup.select_one(selector)
                if element is None:
                    continue
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
