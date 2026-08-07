"""CodeView — a professional, custom-painted diff/code viewer.

Everything is painted directly in ``paintEvent``, so rendering cost is
proportional to the *visible* lines, never the document size — 100K+ lines
scroll as smoothly as 100. The viewer provides:

- line-number gutter with per-line diff markers (+, −, ~) and fold toggles
- syntax highlighting (lazy, per visible line, cached)
- intra-line word/character highlight segments (from the compare engine)
- collapsible unchanged sections (fold headers in the gutter, click to toggle)
- search with match highlighting and next/previous navigation
- replace (next / all) on the pane's in-memory buffer (``contentChanged``)
- a minimap strip on the right edge (click/drag to scroll)
- difference markers painted on the vertical scrollbar
- zoom (Ctrl+wheel, Ctrl+= / Ctrl+−) and status information

The pane is read-only by default; ``replace_*`` mutates the in-memory lines
and emits ``contentChanged`` so the Compare module can re-diff live.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QAbstractScrollArea, QScrollBar, QWidget

from devworkbench.ui.theme import current_colors
from devworkbench.ui.widgets.fold_model import FoldModel
from devworkbench.ui.widgets.search_model import Match, find_matches, next_index
from devworkbench.ui.widgets.syntax import (
    ATTR,
    BOOL,
    COMMENT,
    DECORATOR,
    DEFAULT,
    FUNC,
    KEYWORD,
    NUMBER,
    STRING,
    TAG,
    TYPE,
    block_comment_states,
    highlight_line,
)

# --- presentation tables ----------------------------------------------------

_STATE_BG = {
    "added": ("green", 0.13),
    "removed": ("red", 0.12),
    "changed": ("amber", 0.12),
    "header": ("accent", 0.10),
}

_STATE_MARK = {"added": "+", "removed": "\u2212", "changed": "~", "header": "@"}

_SEGMENT_FG = {"removed": "red", "added": "green", "changed": "amber", "equal": "text"}

_TOKEN_COLOR = {
    KEYWORD: "accent",
    STRING: "green",
    COMMENT: "text3",
    NUMBER: "amber",
    DECORATOR: "purple",
    TYPE: "purple",
    FUNC: "cyan",
    TAG: "accent",
    ATTR: "amber",
    BOOL: "purple",
    DEFAULT: "text",
}

_GUTTER_MARKER_W = 18.0
_MINIMAP_W = 76
_MINIMAP_MIN_W = 52
_TAB_WIDTH = 4
_SYNTAX_CACHE_LIMIT = 4096


class DiffScrollBar(QScrollBar):
    """Vertical scrollbar that paints colored difference markers.

    Run-length encodes line states once per content load, so painting is a
    handful of small rects even for huge files.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Vertical, parent)
        self._ranges: list[tuple[float, float, str]] = []

    def set_states(self, states: list[str]) -> None:
        ranges: list[tuple[float, float, str]] = []
        total = len(states)
        if not total:
            self._ranges = []
            return
        index = 0
        while index < total:
            state = states[index]
            if state not in ("added", "removed", "changed"):
                index += 1
                continue
            end = index
            while end < total and states[end] == state:
                end += 1
            ranges.append((index / total, end / total, state))
            index = end
        self._ranges = ranges
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if not self._ranges:
            return
        colors = current_colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        track = self.rect().adjusted(3, 4, -4, -4)
        for start, end, state in self._ranges:
            token = "green" if state == "added" else "red" if state == "removed" else "amber"
            color = QColor(colors[token])
            color.setAlphaF(0.85)
            painter.fillRect(
                QRectF(track.x(), track.top() + start * track.height(), 4, max(2.0, (end - start) * track.height())),
                color,
            )
        painter.end()


def _display_len(text: str) -> int:
    """Length of a line with tabs expanded (for horizontal scroll math)."""
    if "\t" not in text:
        return len(text)
    return sum(_TAB_WIDTH if ch == "\t" else 1 for ch in text)


class CodeView(QAbstractScrollArea):
    """Monospace diff pane with gutter, syntax, folds, search, minimap."""

    contentChanged = Signal()  # after replace_* mutations (Compare re-diffs)
    searchStateChanged = Signal(int, int)  # (match_count, current_index)
    foldStateChanged = Signal(int, int)  # (folded_count, hidden_count)
    scrollChanged = Signal(int)  # (top logical line index)
    zoomChanged = Signal(float)  # (font point size)

    def __init__(self, mono_size: float = 12.0, kind: str = "text", minimap: bool = True) -> None:
        super().__init__()
        self._kind = kind
        self._lines: list[str] = []
        self._states: list[str] = []
        self._segments: dict[int, list[tuple[str, str]]] = {}
        self._syntax_cache: dict[int, list[tuple[str, str]]] = {}
        self._block_states: list[bool] = []
        self._max_width = 0
        self._fold = FoldModel()
        self._minimap_enabled = minimap
        self._minimap_bands: list[tuple[int, int, str]] = []  # (visual_start, visual_end, color_token)
        self._point = mono_size
        self._query = ""
        self._case_sensitive = True
        self._matches: list[Match] = []
        self._current = -1

        self._setup_font()
        self.setVerticalScrollBar(DiffScrollBar(self))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.viewport().setMouseTracking(True)
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.horizontalScrollBar().valueChanged.connect(self.viewport().update)
        self._last_emitted_top = -1

    # -- public API ------------------------------------------------------------

    def set_kind(self, kind: str) -> None:
        if kind != self._kind:
            self._kind = kind
            self._syntax_cache.clear()
            self._block_states = block_comment_states(self._lines, kind)
            self.viewport().update()

    def set_content(self, lines: list[str], states: list[str] | None = None) -> None:
        """Replace content. ``states[i]`` ∈ {\"\", added, removed, changed, header}."""
        self._lines = list(lines)
        self._states = list(states or [])
        while len(self._states) < len(self._lines):
            self._states.append("")
        self._segments = {}
        self._syntax_cache = {}
        self._block_states = block_comment_states(self._lines, self._kind)
        self._max_width = max((_display_len(line) for line in self._lines), default=0)
        self._matches = []
        self._current = -1
        self._query = ""
        self._fold.reset(len(self._lines), self._states)
        self.verticalScrollBar().set_states(self._states)
        self._rebuild_minimap()
        self._update_scroll_range()
        self._update_hscroll_range()
        self.verticalScrollBar().setValue(0)
        self.horizontalScrollBar().setValue(0)
        self._last_emitted_top = -1
        self.scrollChanged.emit(0)
        self.viewport().update()

    def set_segments(self, segments: dict[int, list[tuple[str, str]]]) -> None:
        """Intra-line highlight segments per line index (after set_content)."""
        self._segments = {index: list(pair) for index, pair in segments.items()}
        self.viewport().update()

    def content_lines(self) -> list[str]:
        return list(self._lines)

    # -- search -------------------------------------------------------------

    def find(self, query: str, case_sensitive: bool = True) -> int:
        """Run a search over this pane; returns the match count."""
        self._query = query
        self._case_sensitive = case_sensitive
        self._matches = find_matches(self._lines, query, case_sensitive)
        self._current = -1
        self.searchStateChanged.emit(len(self._matches), self._current)
        self.viewport().update()
        return len(self._matches)

    def goto_match(self, forward: bool) -> bool:
        """Move to the next/previous match; returns True if one exists."""
        if not self._matches:
            return False
        nxt = next_index(self._matches, self._current, forward)
        self._current = nxt if nxt is not None else -1
        match = self._matches[self._current]
        self._fold.unfold_containing(match.line)
        self._scroll_to_line(self._fold.visual_of(match.line))
        self.searchStateChanged.emit(len(self._matches), self._current)
        self.viewport().update()
        return True

    def replace_next(self, replacement: str) -> bool:
        """Replace the current match in the in-memory buffer; re-search."""
        if not self._matches:
            return False
        if self._current < 0:
            self._current = 0
        match = self._matches[self._current]
        line = self._lines[match.line]
        self._lines[match.line] = line[: match.col] + replacement + line[match.end :]
        self._recompute_after_edit()
        # Jump to the next match (standard replace-and-find-next behavior).
        self._current = -1
        self.goto_match(True)
        self.contentChanged.emit()
        return True

    def replace_all(self, query: str, replacement: str, case_sensitive: bool = True) -> int:
        """Replace every occurrence of ``query``; returns how many changed."""
        matches = find_matches(self._lines, query, case_sensitive)
        if not matches:
            return 0
        by_line: dict[int, list[Match]] = {}
        for match in matches:
            by_line.setdefault(match.line, []).append(match)
        for line_index, line_matches in by_line.items():
            line = self._lines[line_index]
            parts: list[str] = []
            cursor = 0
            for match in sorted(line_matches, key=lambda m: m.col):
                parts.append(line[cursor : match.col])
                parts.append(replacement)
                cursor = match.end
            parts.append(line[cursor:])
            self._lines[line_index] = "".join(parts)
        self._recompute_after_edit()
        self.contentChanged.emit()
        return len(matches)

    def clear_search(self) -> None:
        self._query = ""
        self._matches = []
        self._current = -1
        self.searchStateChanged.emit(0, -1)
        self.viewport().update()

    def _recompute_after_edit(self) -> None:
        self._syntax_cache.clear()
        self._segments = {}
        self._matches = find_matches(self._lines, self._query, self._case_sensitive)
        self._current = -1
        self.searchStateChanged.emit(len(self._matches), -1)
        self.viewport().update()

    # -- navigation ----------------------------------------------------------

    def goto_line(self, index: int) -> None:
        """Scroll so ``index`` is at the top; unfolds folds hiding it."""
        index = max(0, min(index, len(self._lines) - 1)) if self._lines else 0
        self._fold.unfold_containing(index)
        self._scroll_to_line(self._fold.visual_of(index))
        self.viewport().update()

    def toggle_fold_at(self, index: int) -> None:
        folded = self._fold.toggle(index)
        self._rebuild_minimap()
        self._update_scroll_range()
        # Keep the header line on screen after collapsing.
        if folded:
            self._scroll_to_line(self._fold.visual_of(index))
        self.foldStateChanged.emit(self._fold.folded_count, self._fold.hidden_count)
        self.viewport().update()

    def fold_all(self) -> None:
        for fold in self._fold.foldables:
            self._fold.toggle(fold.start)
        self._rebuild_minimap()
        self._update_scroll_range()
        self.foldStateChanged.emit(self._fold.folded_count, self._fold.hidden_count)
        self.viewport().update()

    def unfold_all(self) -> None:
        self._fold.unfold_all()
        self._rebuild_minimap()
        self._update_scroll_range()
        self.foldStateChanged.emit(0, 0)
        self.viewport().update()

    # -- zoom / status ---------------------------------------------------------

    def zoom_in(self) -> None:
        self._set_point(self._point + 1.0)

    def zoom_out(self) -> None:
        self._set_point(self._point - 1.0)

    def zoom_reset(self) -> None:
        self._set_point(12.0)

    def _set_point(self, point: float) -> None:
        self._point = max(7.0, min(24.0, point))
        self._setup_font()
        self._update_scroll_range()
        self._update_hscroll_range()
        self.zoomChanged.emit(self._point)
        self.viewport().update()

    def status_info(self) -> dict:
        return {
            "total_lines": len(self._lines),
            "visible_lines": self._fold.visible_count,
            "folded": self._fold.folded_count,
            "hidden": self._fold.hidden_count,
            "matches": len(self._matches),
            "current_match": self._current,
            "zoom": self._point,
            "kind": self._kind,
            "scroll_top": self._fold.logical_of(self.verticalScrollBar().value() // self._line_height()),
        }

    def match_count(self) -> int:
        return len(self._matches)

    def current_match_index(self) -> int:
        return self._current

    def refresh(self) -> None:
        """Re-read theme colors and repaint (call after a theme switch)."""
        self.verticalScrollBar().update()
        self.viewport().update()

    def clear(self) -> None:
        self.set_content([])

    # -- internals ---------------------------------------------------------------

    def _setup_font(self) -> None:
        font = QFont()
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setFamilies(["SF Mono", "Menlo", "Consolas", "DejaVu Sans Mono", "monospace"])
        font.setPointSizeF(self._point)
        self.setFont(font)
        self._metrics = QFontMetricsF(font)
        self._char_w = self._metrics.horizontalAdvance("9")
        self._tab_w = self._char_w * _TAB_WIDTH

    def _line_height(self) -> int:
        return max(14, int(self._metrics.height()))

    def _gutter_width(self) -> int:
        digits = max(3, len(str(len(self._lines))))
        return int(_GUTTER_MARKER_W + self._char_w * digits + 16)

    def _minimap_width(self) -> int:
        if not self._minimap_enabled:
            return 0
        return max(_MINIMAP_MIN_W, min(_MINIMAP_W, int(self.viewport().width() * 0.08)))

    def _content_height(self) -> int:
        return self._fold.visible_count * self._line_height()

    def _update_scroll_range(self) -> None:
        bar = self.verticalScrollBar()
        bar.setRange(0, max(0, self._content_height() - self.viewport().height()))
        self.viewport().update()

    def _update_hscroll_range(self) -> None:
        """Horizontal range from the longest (tab-expanded) line, so long
        lines can be scrolled instead of being silently clipped."""
        bar = self.horizontalScrollBar()
        gutter_w = self._gutter_width()
        reach = self._max_width * self._char_w - (self.viewport().width() - gutter_w - self._minimap_width())
        bar.setRange(0, max(0, reach))

    def _scroll_to_line(self, visual: int) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(visual * self._line_height())

    def _on_scroll(self, _value: int) -> None:
        self.viewport().update()
        top = self._fold.logical_of(self.verticalScrollBar().value() // self._line_height())
        if top != self._last_emitted_top:
            self._last_emitted_top = top
            self.scrollChanged.emit(top)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_scroll_range()
        self._update_hscroll_range()
        self._rebuild_minimap()

    # -- minimap -----------------------------------------------------------------

    def _rebuild_minimap(self) -> None:
        if not self._minimap_enabled:
            return
        bands: list[tuple[int, int, str]] = []
        total = len(self._lines)
        if not total:
            self._minimap_bands = []
            return
        # Aggregate logical lines into visual bands (fold-aware).
        runs: list[tuple[int, int, str]] = []
        index = 0
        while index < total:
            state = self._states[index]
            token = ""
            if state == "added":
                token = "green"
            elif state == "removed":
                token = "red"
            elif state == "changed":
                token = "amber"
            if not token:
                index += 1
                continue
            end = index
            while end < total and self._states[end] == state:
                end += 1
            visual_start = self._fold.visual_of(index)
            visual_end = self._fold.visual_of(end - 1) + 1 if end > 0 else visual_start
            if visual_end > visual_start:
                runs.append((visual_start, visual_end, token))
            index = end
        self._minimap_bands = runs

    # -- painting -------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        colors = current_colors()
        painter = QPainter(self.viewport())
        painter.setFont(self.font())
        painter.fillRect(event.rect(), QColor(colors["bg"]))

        viewport_w = self.viewport().width()
        gutter_w = self._gutter_width()
        minimap_w = self._minimap_width()
        text_area_w = viewport_w - gutter_w - minimap_w
        line_h = self._line_height()
        scroll_x = self.horizontalScrollBar().value()

        total_visible = self._fold.visible_count
        if not total_visible or text_area_w <= 0:
            painter.end()
            return

        vis_top = self.verticalScrollBar().value() // line_h
        vis_bottom = vis_top + self.viewport().height() // line_h + 1
        logical = self._fold.logical_of(vis_top)
        n = len(self._lines)

        # Vertical separator lines.
        painter.fillRect(QRectF(gutter_w, 0, 1, self.viewport().height()), QColor(colors["border"]))
        if minimap_w:
            painter.fillRect(QRectF(viewport_w - minimap_w, 0, 1, self.viewport().height()), QColor(colors["border"]))

        folds = self._fold.sorted_active()
        fold_cursor = 0
        while len(folds) > 0 and fold_cursor < len(folds) and folds[fold_cursor].end <= logical:
            fold_cursor += 1

        y = (self._fold.visual_of(logical) - vis_top) * line_h
        pen = QPen(QColor(colors["text"]))

        while y < self.viewport().height() and logical < n:
            # Skip hidden spans (we land on a fold header via logical_of).
            if fold_cursor < len(folds) and folds[fold_cursor].start == logical:
                # Fold header line: draw normally, then jump past the fold.
                self._paint_line(painter, colors, logical, y, line_h, gutter_w, minimap_w, scroll_x, pen)
                logical = folds[fold_cursor].end
                fold_cursor += 1
                y += line_h
                continue
            self._paint_line(painter, colors, logical, y, line_h, gutter_w, minimap_w, scroll_x, pen)
            logical += 1
            y += line_h

        self._paint_minimap(painter, colors, gutter_w, minimap_w, vis_top, vis_bottom)
        painter.end()

    def _paint_line(self, painter, colors, logical, y, line_h, gutter_w, minimap_w, scroll_x, pen) -> None:
        """Draw one visible line: background, match rects, gutter, text."""
        line = self._lines[logical]
        state = self._states[logical] if logical < len(self._states) else ""
        text_x = gutter_w - scroll_x

        # --- background tint per diff state ---------------------------------
        if state in _STATE_BG:
            token, alpha = _STATE_BG[state]
            bg = QColor(colors[token])
            bg.setAlphaF(alpha)
            painter.fillRect(QRectF(gutter_w, y, self.viewport().width() - gutter_w - self._minimap_width(), line_h), bg)

        # --- search match rectangles ------------------------------------------
        match_rects: list[tuple[float, float, bool]] = []
        for index, match in enumerate(self._matches):
            if match.line != logical:
                continue
            display_col = self._raw_to_display(logical, match.col)
            display_end = self._raw_to_display(logical, match.end)
            is_current = index == self._current
            match_rects.append((text_x + display_col * self._char_w, (display_end - display_col) * self._char_w, is_current))
        for rect_x, rect_w, is_current in match_rects:
            token = "accent" if is_current else "accentSoft"
            color = QColor(colors[token])
            color.setAlphaF(0.9 if is_current else 0.35)
            painter.fillRect(QRectF(rect_x, y + 1, max(rect_w, self._char_w), line_h - 2), color)

        # --- gutter --------------------------------------------------------------
        # Fold toggle triangle (foldable header lines).
        fold = self._fold.foldable_at(logical)
        folded = self._fold.is_folded(logical)
        if fold is not None:
            tri_x = (_GUTTER_MARKER_W - 6) / 2
            tri_y = y + line_h / 2
            tri_color = QColor(colors["text3"])
            if folded:  # ▶ (points right: collapsed)
                triangle = QPolygonF(
                    [
                        QPointF(tri_x, tri_y - 2.5),
                        QPointF(tri_x + 5, tri_y),
                        QPointF(tri_x, tri_y + 2.5),
                    ]
                )
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(tri_color)
                painter.drawPolygon(triangle)
                painter.setBrush(Qt.BrushStyle.NoBrush)
            else:  # ▼ (points down: expanded)
                triangle = QPolygonF(
                    [
                        QPointF(tri_x - 2.5, tri_y - 2),
                        QPointF(tri_x + 2.5, tri_y - 2),
                        QPointF(tri_x, tri_y + 3),
                    ]
                )
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(tri_color)
                painter.drawPolygon(triangle)
                painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(pen)
        # Diff state marker.
        if state in _STATE_MARK:
            token = "green" if state == "added" else "red" if state == "removed" else "amber" if state == "changed" else "accent"
            painter.setPen(QColor(colors[token]))
            painter.drawText(
                QRectF(0, y, _GUTTER_MARKER_W, line_h),
                Qt.AlignmentFlag.AlignCenter,
                _STATE_MARK[state],
            )
            painter.setPen(pen)
        # Line number.
        painter.setPen(QColor(colors["text3"]))
        painter.drawText(
            QRectF(_GUTTER_MARKER_W, y, self._gutter_width() - _GUTTER_MARKER_W - 8, line_h),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            str(logical + 1),
        )
        painter.setPen(pen)

        # --- text runs -----------------------------------------------------------
        if not line:
            return
        runs = self._line_runs(logical)
        x = text_x
        text_rect_w = self.viewport().width() - self._minimap_width() - gutter_w
        for run_text, token in runs:
            if not run_text:
                continue
            display = run_text.replace("\t", " " * _TAB_WIDTH)
            if not display:
                continue
            width = len(display) * self._char_w
            if x + width < gutter_w or x > gutter_w + text_rect_w:  # culled
                x += width
                continue
            painter.setPen(self._run_color(colors, token))
            painter.drawText(QRectF(x, y, width + 1, line_h), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, display)
            x += width

    def _run_color(self, colors, token) -> QColor:
        return QColor(colors.get(token, colors["text"]))

    def _line_runs(self, logical: int) -> list[tuple[str, str]]:
        """Color runs for a line: intra-line segments win, else syntax."""
        segments = self._segments.get(logical)
        if segments:
            return [(text, _SEGMENT_FG.get(state, "text")) for state, text in segments]
        cached = self._syntax_cache.get(logical)
        if cached is not None:
            return cached
        block_active = logical < len(self._block_states) and self._block_states[logical]
        runs = highlight_line(self._lines[logical], self._kind, block_active=block_active)
        if len(self._syntax_cache) >= _SYNTAX_CACHE_LIMIT:
            self._syntax_cache.clear()
        self._syntax_cache[logical] = runs
        return runs

    def _raw_to_display(self, logical: int, raw_col: int) -> int:
        """Map a raw-text column to a tab-expanded display column."""
        if raw_col <= 0:
            return raw_col
        display = 0
        for ch in self._lines[logical][:raw_col]:
            display += _TAB_WIDTH if ch == "\t" else 1
        return display

    # -- minimap painting --------------------------------------------------------

    def _paint_minimap(self, painter, colors, gutter_w, minimap_w, vis_top, vis_bottom) -> None:
        if not minimap_w:
            return
        x0 = self.viewport().width() - minimap_w
        painter.fillRect(QRectF(x0, 0, minimap_w, self.viewport().height()), QColor(colors["surface"]))
        total = self._fold.visible_count
        height = self.viewport().height()
        if total == 0:
            return
        for band_start, band_end, token in self._minimap_bands:
            if band_end <= 0:
                continue
            top = band_start / total * height
            bottom = band_end / total * height
            if bottom < 0 or top > height:
                continue
            color = QColor(colors[token])
            color.setAlphaF(0.85)
            painter.fillRect(QRectF(x0 + 2, top, minimap_w - 4, max(1.5, bottom - top)), color)
        # Viewport indicator.
        top_frac = vis_top / total
        bottom_frac = min(1.0, vis_bottom / total)
        ind = QColor(colors["text3"])
        ind.setAlphaF(0.30)
        painter.fillRect(QRectF(x0 + 1, top_frac * height, minimap_w - 2, max(3, (bottom_frac - top_frac) * height)), ind)

    # -- mouse ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        x = pos.x()
        minimap_w = self._minimap_width()
        if x >= self.viewport().width() - minimap_w and minimap_w:
            self._minimap_dragging = True
            self._minimap_scroll_to(x, pos.y())
            event.accept()
            return
        if x < self._gutter_width():
            # Clicking the marker strip toggles a fold; clicking a number
            # navigates to that line.
            line_h = self._line_height()
            vis = (self.verticalScrollBar().value() + pos.y()) // line_h
            logical = self._fold.logical_of(vis)
            if x < _GUTTER_MARKER_W and self._fold.foldable_at(logical) is not None:
                self.toggle_fold_at(logical)
                event.accept()
                return
            self.goto_line(logical)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if getattr(self, "_minimap_dragging", False):
            self._minimap_scroll_to(event.position().x(), event.position().y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._minimap_dragging = False
        super().mouseReleaseEvent(event)

    def _minimap_scroll_to(self, x: float, y: float) -> None:
        total = self._fold.visible_count
        if total <= 0:
            return
        fraction = y / max(1, self.viewport().height())
        visual = int(fraction * total)
        bar = self.verticalScrollBar()
        bar.setValue(min(bar.maximum(), visual * self._line_height()))

    def wheelEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            elif delta < 0:
                self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(560, 480)
