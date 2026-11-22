"""WP5. Composition, HTML/CSS generation, rendering, page-count fitting."""

from rooted.layout.compose import compose
from rooted.layout.fit import fit_to_pages
from rooted.layout.html import build_html
from rooted.layout.render import get_renderer

__all__ = ["compose", "build_html", "get_renderer", "fit_to_pages"]
