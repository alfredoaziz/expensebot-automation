"""Turns a bank notification email's HTML into a flat list of text lines.

Bank emails are layout tables, so a "label" cell and its "value" cell end
up as consecutive lines — which is exactly what the parsers in
gmail/parsers.py look up. Inline tags (span/strong/a) do NOT break a line:
blu splits one amount across spans ("Rp" + "600.000" + ",00") that must
stay together as "Rp600.000,00".
"""

from __future__ import annotations

from html.parser import HTMLParser

_BLOCK_TAGS = frozenset(
    {
        "br",
        "p",
        "div",
        "td",
        "th",
        "tr",
        "table",
        "li",
        "ul",
        "ol",
        "h1",
        "h2",
        "h3",
        "h4",
        "hr",
    }
)
_SKIP_TAGS = frozenset({"style", "script", "head", "title"})


class _LineCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._current: list[str] = []
        self._skip_depth = 0

    def flush(self) -> None:
        line = " ".join("".join(self._current).split())
        if line:
            self.lines.append(line)
        self._current = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.flush()

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag in _BLOCK_TAGS:
            self.flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self.flush()

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._current.append(data)


def html_to_lines(html: str) -> list[str]:
    collector = _LineCollector()
    collector.feed(html)
    collector.close()
    collector.flush()
    return collector.lines


def plain_text_to_lines(text: str) -> list[str]:
    """Fallback for emails without an HTML part: one line per row/cell
    (plain-text bank templates often separate label and value with "|"
    or tabs)."""
    lines: list[str] = []
    for raw in text.splitlines():
        for cell in raw.replace("\t", "|").split("|"):
            cleaned = " ".join(cell.split())
            if cleaned:
                lines.append(cleaned)
    return lines
