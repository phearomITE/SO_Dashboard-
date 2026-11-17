from __future__ import annotations

from urllib.parse import urlsplit
from openpyxl.styles import Alignment, Font


def set_clickable_link(cell, url: str, label: str) -> None:
    """Use a native Excel formula without worksheet hyperlink relationships."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError("Excel link requires an absolute HTTP(S) URL")
    if any(ord(char) < 32 for char in url):
        raise ValueError("Excel link contains a control character")
    escaped_url = url.replace('"', '""')
    escaped_label = label.replace('"', '""')
    cell.hyperlink = None
    cell.value = f'=HYPERLINK("{escaped_url}","{escaped_label}")'
    cell.font = Font(name="Calibri", color="0563C1", underline="single")
    cell.alignment = Alignment(vertical="top", wrap_text=True)
