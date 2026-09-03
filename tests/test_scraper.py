"""Tests for guide scraper HTML rendering."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scraper import _render_element, _is_nested_in_seen, _CONTENT_TAGS

from bs4 import BeautifulSoup, Tag


def _soup(html: str):
    return BeautifulSoup(html, "html.parser")


class TestRenderElement:
    def test_paragraph(self):
        el = _soup("<p>Hello world</p>").find("p")
        text = _render_element(el)
        assert "Hello world" in text

    def test_code_block_preserved(self):
        el = _soup('<pre><code>{\n  "key": "value"\n}</code></pre>').find("pre")
        text = _render_element(el)
        assert "```" in text
        assert '"key": "value"' in text

    def test_unordered_list(self):
        el = _soup("<ul><li>First</li><li>Second</li></ul>").find("ul")
        text = _render_element(el)
        assert "First" in text
        assert "Second" in text

    def test_table_to_markdown(self):
        html = "<table><tr><th>Name</th><th>Value</th></tr><tr><td>A</td><td>1</td></tr></table>"
        el = _soup(html).find("table")
        text = _render_element(el)
        assert "Name" in text
        assert "Value" in text


class TestNestedDetection:
    def test_nested_in_seen(self):
        html = "<div><p>Inner</p></div>"
        soup = _soup(html)
        div = soup.find("div")
        p = soup.find("p")
        seen = {id(div)}
        assert _is_nested_in_seen(p, seen)

    def test_not_nested(self):
        html = "<div><p>Inner</p></div>"
        soup = _soup(html)
        div = soup.find("div")
        assert not _is_nested_in_seen(div, set())
