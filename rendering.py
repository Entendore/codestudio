from __future__ import annotations
from typing import Dict, List, Optional, Tuple, Union, Final
from collections import OrderedDict
from datetime import datetime
from PySide6.QtCore import Qt, QRect, QPoint
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics, QImage, QLinearGradient, QPainter, QPainterPath, QPen
import bisect, os
from .core import THEMES, Theme
from .text import Tokenizer, KeyboardOverlay, TypingAnimator
class CodeRenderer:
    TOKEN_COLOR_MAP: Final[Dict[str, str]] = {"keyword":"keyword","builtin":"builtin","string":"string","number":"number","comment":"comment","decorator":"decorator","function":"function","class_name":"class_name","operator":"operator","boolean":"number","constant":"number","preprocessor":"decorator","variable":"function","symbol":"string","macro":"function","tag":"keyword","attr":"builtin","selector":"keyword","property":"builtin","value":"string","hex_color":"number","heading":"keyword","bold":"keyword","italic":"keyword","code":"string","link":"function","punctuation":"comment","fstring":"string","lifetime":"constant","annotation":"decorator"}
    CURSOR_BLINK: Final[float] = 0.53
    def __init__(self, width: int, height: int, theme_name: str = "Dracula", font_family: str = "Consolas", font_size: int = 22, show_line_numbers: bool = True, show_window_chrome: bool = True, padding: int = 24, tab_size: int = 4, title_text: str = "main.py", language: str = "Python", keyboard_overlay: Optional[KeyboardOverlay] = None, bg_image_path: Optional[str] = None, total_code_lines: int = 0, cursor_glow: bool = True, show_watermark: bool = False, accent_color: Optional[str] = None):
        self.width = width; self.height = height; self.theme = THEMES.get(theme_name, THEMES["Dracula"]); self.theme_name = theme_name; self.font_family = font_family; self.font_size = font_size; self.show_line_numbers = show_line_numbers; self.show_window_chrome = show_window_chrome; self.padding = padding; self.tab_size = tab_size; self.title_text = title_text; self.language = language; self.keyboard_overlay = keyboard_overlay; self.total_code_lines = total_code_lines; self.cursor_glow = cursor_glow; self.show_watermark = show_watermark; self.accent_color = accent_color or getattr(self.theme, "junior_accent", "#7aa2f7")
        _families = [font_family, "JetBrains Mono", "DejaVu Sans Mono", "Liberation Mono", "Courier New", "monospace"]; _available = QFontDatabase.families(); chosen = "monospace"
        for f in _families:
            if f in _available: chosen = f; break
        self.font = QFont(chosen, font_size); self.font.setFamilies(_families); self.font.setFixedPitch(True); self.fm = QFontMetrics(self.font); self.char_w = self.fm.horizontalAdvance("M"); self.line_h = self.fm.height()
        self._qcolors = {k: QColor(v) for k, v in self.theme.__dict__.items() if isinstance(v, str) and v.startswith("#")}; self._qc_fg = self._qcolors.get("foreground", QColor("#f8f8f2")); self._qc_ln = self._qcolors.get("line_number", QColor("#6272a4")); self._qc_ln_active = QColor(self._qc_fg).darker(120); self._qc_cursor = self._qcolors.get("cursor", QColor("#f8f8f2")); self._qc_current_line = self._qcolors.get("current_line", QColor("#44475a")); self._qc_accent = QColor(self.accent_color)
        self.bg_image = (QImage(bg_image_path) if bg_image_path and os.path.exists(bg_image_path) else None); self._build_bg_cache(); self._layout_nv: Union[str, int] = -1; self._layout_lines: List[str] = []; self._layout_offsets: List[int] = []; self._layout_cursor_line: int = 0; self._line_cache: "OrderedDict[str, List[int]]" = OrderedDict(); self._tab_advance = self.char_w * self.tab_size; self._tok_cache: "OrderedDict[str, List[str]]" = OrderedDict()
    @staticmethod
    def auto_font_size(code_lines: int, width: int, height: int, padding: int = 24, show_chrome: bool = True, show_ln: bool = True, tab_size: int = 4, code: Optional[str] = None, font_family: str = "Consolas", keyboard_h: int = 0) -> int:
        chrome_h = 42 if show_chrome else 0; kb_used = min(keyboard_h, (height - 2 * padding) // 3) if keyboard_h > 0 else 0; rect_h = height - 2 * padding - kb_used - chrome_h; rect_w = width - 2 * padding - 8
        if rect_h < 20 or rect_w < 40 or code_lines < 1: return 14
        max_chars = 80
        if code: max_chars = max(max(len(line.replace("\t", " " * tab_size)) for line in code.split("\n")), 1)
        def _ln_w(cw: int) -> int: return (len(str(code_lines)) * cw + 16) if show_ln else 0
        max_font = max(14, min(48, int(width / 80))); target_vis = min(code_lines, 35); max_check = min(max_chars, 120); lo, hi, best = 8, max_font, 14
        while lo <= hi:
            mid = (lo + hi) // 2; fm = QFontMetrics(QFont(font_family, mid)); lh = fm.height()
            if target_vis * lh <= rect_h:
                cw = fm.horizontalAdvance("M")
                if max_check * cw + _ln_w(cw) <= rect_w: best = mid; lo = mid + 1
                else: hi = mid - 1
            else: hi = mid - 1
        return max(8, best)
    def _get_line_layout(self, line: str) -> List[int]:
        cached = self._line_cache.get(line)
        if cached is not None: return cached
        char_x: List[int] = []; x = 0; tab = self._tab_advance; ham = self.fm.horizontalAdvance
        for ch in line: char_x.append(x); x += tab if ch == "\t" else ham(ch)
        if len(self._line_cache) >= 512: self._line_cache.popitem(last=False)
        self._line_cache[line] = char_x; return char_x
    def _tokenize_to_colors(self, text: str) -> List[str]:
        cached = self._tok_cache.get(text)
        if cached is not None: return cached
        tokens = Tokenizer.tokenize(text, self.language); colors = ["foreground"] * len(text); pos = 0; get = self.TOKEN_COLOR_MAP.get; n = len(colors)
        for ttype, ttxt in tokens:
            ckey = get(ttype, "foreground"); end = min(pos + len(ttxt), n); colors[pos:end] = [ckey] * (end - pos); pos = end
        if len(self._tok_cache) > 256: self._tok_cache.popitem(last=False)
        self._tok_cache[text] = colors; return colors
    def _build_bg_cache(self) -> None:
        self._bg = QImage(self.width, self.height, QImage.Format_RGB32); self._bg.fill(QColor(self.theme.background)); p = QPainter(self._bg); p.setRenderHint(QPainter.Antialiasing)
        if self.bg_image: scaled = self.bg_image.scaled(self.width, self.height, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation); p.drawImage((self.width - scaled.width()) // 2, (self.height - scaled.height()) // 2, scaled); p.fillRect(0, 0, self.width, self.height, QColor(0, 0, 0, 130))
        else: bg = self._qcolors.get("background", QColor("#282a36")); g = QLinearGradient(0, 0, 0, self.height); g.setColorAt(0, bg.lighter(105)); g.setColorAt(1, bg); p.fillRect(0, 0, self.width, self.height, g)
        w, h, pad = self.width, self.height, self.padding; chrome_h = 42 if self.show_window_chrome else 0; kb_reserve = (min(self.keyboard_overlay.height_needed(), (h - 2 * pad) // 3) if self.keyboard_overlay else 0); wx, wy, ww, wh = pad, pad, w - 2 * pad, h - 2 * pad - kb_reserve
        p.setPen(Qt.PenStyle.NoPen)
        for s in range(6, 0, -1): p.setBrush(QColor(0, 0, 0, int(40 - s * 5))); p.drawRoundedRect(wx - s, wy - s + (s // 2), ww + 2 * s, wh + 2 * s, 12 + s, 12 + s)
        p.setPen(QColor(self.theme.window_border)); p.setBrush(QColor(self.theme.background)); p.drawRoundedRect(wx, wy, ww, wh, 12, 12)
        if self.show_window_chrome:
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(self.theme.title_bar)); p.drawRoundedRect(wx, wy, ww, chrome_h, 12, 12); p.drawRect(wx, wy + chrome_h - 12, ww, 12)
            for i, color in enumerate(["#ff5f56", "#ffbd2e", "#27c93f"]): p.setBrush(QColor(color)); p.setPen(Qt.PenStyle.NoPen); p.drawEllipse(QPoint(wx + 20 + i * 24, wy + 19), 7, 7)
            p.setPen(QColor(self.theme.title_text)); p.setFont(QFont(self.font_family, max(8, self.font_size // 3))); p.drawText(QRect(wx, wy, ww, chrome_h), Qt.AlignCenter, self.title_text)
            p.setPen(QPen(self._qc_accent, 2)); p.drawLine(wx, wy + chrome_h, wx + ww, wy + chrome_h); code_top = wy + chrome_h
        else: code_top = wy
        self._code_rect = QRect(wx + 4, code_top, ww - 8, wh - (code_top - wy))
        if self.keyboard_overlay: self.keyboard_overlay.reposition(wy + wh + max(8, h // 60))
        p.end()
    def render_frame(self, visible_text: str, vis_err: List[bool], cursor_visible: bool = True, target: Optional[QImage] = None, active_char: Optional[str] = None, key_flash: float = 0.0) -> QImage:
        img = target if target is not None else QImage(self.width, self.height, QImage.Format_RGB32)
        if target is None: img.fill(QColor(self.theme.background))
        p = QPainter(img); p.setRenderHint(QPainter.TextAntialiasing); p.drawImage(0, 0, self._bg); cr = self._code_rect; colors = self._tokenize_to_colors(visible_text); fg = self._qc_fg; qc = self._qcolors; vis_qc = [qc.get(ck, fg) for ck in colors]
        if visible_text != self._layout_nv:
            self._layout_lines = visible_text.split("\n"); offsets: List[int] = []; off = 0
            for ln in self._layout_lines: offsets.append(off); off += len(ln) + 1
            self._layout_offsets = offsets; self._layout_cursor_line = visible_text.count("\n"); self._layout_nv = visible_text
        lines = self._layout_lines; line_offsets = self._layout_offsets; cursor_line = self._layout_cursor_line; total_lines = len(self._layout_lines); cr_h, cr_top = cr.height(), cr.top(); max_vis = max(1, -(-cr.height() // self.line_h)); lh_base = self.line_h; scroll = 0; margin_bottom = max(3, max_vis // 4); margin_top = 3
        if cursor_line >= scroll + max_vis - margin_bottom: scroll = max(0, cursor_line - max_vis + margin_bottom + 1)
        if cursor_line < scroll + margin_top: scroll = max(0, cursor_line - margin_top)
        if scroll > max(0, total_lines - max_vis): scroll = max(0, total_lines - max_vis)
        ln_width = (len(str(total_lines + scroll)) * self.char_w + 16) if self.show_line_numbers else 0; current_scroll_line = cursor_line - scroll; line_y_arr: List[int] = []; line_h_arr: List[int] = []; y_acc = cr_top
        for si in range(max_vis):
            li = scroll + si
            if li >= total_lines: break
            line_y_arr.append(y_acc); line_h_arr.append(lh_base); y_acc += lh_base
        n_drawn = len(line_y_arr)
        if 0 <= current_scroll_line < n_drawn:
            p.fillRect(cr.left(), line_y_arr[current_scroll_line], cr.width(), line_h_arr[current_scroll_line], self._qc_current_line); p.setPen(QPen(self._qc_accent, 2)); p.drawLine(cr.left(), line_y_arr[current_scroll_line], cr.left(), line_y_arr[current_scroll_line] + line_h_arr[current_scroll_line])
        if self.show_line_numbers and ln_width > 0:
            sep_x = cr.left() + ln_width; sc = QColor(self._qc_ln); sc.setAlpha(60); p.setPen(QPen(sc, 1)); p.drawLine(sep_x, cr.top(), sep_x, cr.top() + max_vis * lh_base)
        p.setClipRect(cr); p.setFont(self.font); x0 = cr.left() + ln_width; n_vis_chars = len(visible_text)
        for si in range(n_drawn):
            li = scroll + si; lh = line_h_arr[si]; y = line_y_arr[si]; global_off = line_offsets[li]
            if self.show_line_numbers: p.setPen(self._qc_ln); p.drawText(QRect(cr.left(), y, ln_width, lh), Qt.AlignRight | Qt.AlignVCenter, str(li + 1))
            line = lines[li]
            if not line:
                if cursor_visible and li == cursor_line: self._draw_caret(p, int(x0), int(y + lh * 0.15), max(4, lh - 10))
                continue
            char_x = self._get_line_layout(line); cur_qc = vis_qc[global_off] if global_off < n_vis_chars else fg; run_start = 0
            for j in range(1, len(line) + 1):
                next_qc = fg
                if j < len(line): gp = global_off + j; next_qc = vis_qc[gp] if gp < n_vis_chars else fg
                if j == len(line) or next_qc is not cur_qc:
                    run_text = line[run_start:j].replace("\t", " " * self.tab_size); p.setPen(cur_qc); p.drawText(QPoint(int(x0 + char_x[run_start]), int(y + lh * 0.78)), run_text); cur_qc = next_qc; run_start = j
            if cursor_visible and li == cursor_line:
                n_ch = len(line)
                if n_ch < len(char_x): self._draw_caret(p, int(x0 + char_x[n_ch]), int(y + lh * 0.15), max(4, lh - 10))
                else:
                    last_x = char_x[-1] if char_x else 0; last_char = line[-1] if line else " "; last_w = self._tab_advance if last_char == "\t" else self.fm.horizontalAdvance(last_char); self._draw_caret(p, int(x0 + last_x + last_w), int(y + lh * 0.15), max(4, lh - 10), " ")
        p.setClipping(False)
        if self.keyboard_overlay:
            ak = None; kf = 0.0
            if active_char is not None and key_flash > 0: ak = self.keyboard_overlay.resolve_key(active_char); kf = key_flash
            self.keyboard_overlay.draw(p, active_key=ak, flash=kf)
        if self.show_watermark:
            wm_font = QFont(self.font_family, max(8, self.font_size // 3)); p.setFont(wm_font); p.setPen(QColor(self._qc_ln)); wm = f"{datetime.now().strftime('%Y-%m-%d %H:%M')}  |  {self.title_text}"; fm = QFontMetrics(wm_font); p.drawText(self.width - fm.horizontalAdvance(wm) - 20, self.height - 10, wm)
        p.end(); return img
    def _draw_caret(self, p: QPainter, x: int, y: int, h: int, char: str = " ") -> None:
        cw = max(2, self.fm.horizontalAdvance(char)); ch = max(4, h - 2)
        if self.cursor_glow: gc = QColor(self._qc_cursor); gc.setAlpha(80); p.fillRect(x - 2, y - 1, cw + 4, ch + 2, gc); gc.setAlpha(140); p.fillRect(x - 1, y, cw + 2, ch, gc)
        p.fillRect(x, y, cw, ch, self._qc_cursor)
        if char not in (" ", "\t"): p.setPen(QColor(self.theme.background)); p.drawText(QRect(int(x), int(y), int(cw), int(ch)), Qt.AlignCenter, char)
class MultiPaneRenderer:
    __slots__ = ("panes", "frame_w", "frame_h", "num_panes", "pane_layout", "gap", "bg_color", "pane_rects")
    def __init__(self, panes: List[Tuple[CodeRenderer, TypingAnimator]], frame_w: int, frame_h: int, num_panes: int = 2, pane_layout: str = "Side by Side (50/50)", gap: int = 20, bg_color: str = "#0d0d12"):
        self.panes = panes; self.frame_w = frame_w; self.frame_h = frame_h; self.num_panes = max(1, min(num_panes, len(panes))); self.pane_layout = pane_layout; self.gap = gap; self.bg_color = bg_color; self.pane_rects = self._compute_pane_rects()
    def _compute_pane_rects(self) -> List[QRect]:
        W, H, g, n = self.frame_w, self.frame_h, self.gap, self.num_panes
        if n == 1: return [QRect(0, 0, W, H)]
        if n == 2:
            l = self.pane_layout
            match l:
                case "Stacked (50/50)": ph = (H - g) // 2; y_start = (H - (2 * ph + g)) // 2; return [QRect(0, y_start, W, ph), QRect(0, y_start + ph + g, W, ph)]
                case "Junior Big (70/30)": pw_l = int((W - g) * 0.7); return [QRect(0, 0, pw_l, H), QRect(pw_l + g, 0, W - g - pw_l, H)]
                case "Senior Big (70/30)": pw_l = int((W - g) * 0.3); return [QRect(0, 0, pw_l, H), QRect(pw_l + g, 0, W - g - pw_l, H)]
                case _: pw = (W - g) // 2; return [QRect(0, 0, pw, H), QRect(pw + g, 0, pw, H)]
        pw, ph = (W - g) // 2, (H - g) // 2
        return [QRect(0, 0, pw, ph), QRect(pw + g, 0, pw, ph), QRect(0, ph + g, pw, H - ph - g), QRect(pw + g, ph + g, pw, H - ph - g)]
    def duration(self) -> float: return max(anim.duration() for _, anim in self.panes) if self.panes else 0.0
    def char_timestamps_all(self) -> List[Tuple[float, str]]:
        all_ts: List[Tuple[float, str]] = []
        for _, anim in self.panes: all_ts.extend(anim.char_timestamps())
        return all_ts
    def compute_state(self, t: float) -> Tuple:
        states = []
        for renderer, animator in self.panes:
            nv = animator.visible_at(t); cur_vis = True
            if nv > 0:
                idx = bisect.bisect_right(animator._timestamps, t)
                if idx > 0:
                    last_ts = animator.timeline[idx - 1][0]
                    if t - last_ts > 0.25: cur_vis = (int((t - last_ts) / renderer.CURSOR_BLINK) % 2) == 0
            active_char, key_flash = animator.active_key_at(t)
            if not renderer.keyboard_overlay: active_char = None; key_flash = 0.0
            vis_text = animator.text_snapshots[nv]; vis_err = animator.error_snapshots[nv]; states.append((vis_text, vis_err, cur_vis, active_char, round(key_flash, 2)))
        return tuple(states)
    def render_frame(self, t: float, target: Optional[QImage] = None, precomputed_state: Optional[Tuple] = None) -> QImage:
        img = target if target is not None else QImage(self.frame_w, self.frame_h, QImage.Format_RGB32); img.fill(QColor(self.bg_color)); p = QPainter(img); p.setRenderHint(QPainter.SmoothPixmapTransform)
        if precomputed_state is None: precomputed_state = self.compute_state(t)
        sorted_indices = sorted(range(len(self.panes)), key=lambda i: self.pane_rects[i].width() * self.pane_rects[i].height(), reverse=True)
        for i in sorted_indices:
            if i >= len(precomputed_state): break
            vis_text, vis_err, cur_vis, active_char, key_flash = precomputed_state[i]; renderer, animator = self.panes[i]; rect = self.pane_rects[i]
            pane_img = renderer.render_frame(vis_text, vis_err, cur_vis, active_char=active_char, key_flash=key_flash); p.drawImage(rect.topLeft(), pane_img)
        p.end(); return img
