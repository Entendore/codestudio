from __future__ import annotations
from typing import Optional, List, Tuple, Any
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, Future
from PySide6.QtCore import QThread, Signal, Slot, QObject
from .core import EXPORT_FORMATS, ENCODERS, RenderingError, FFmpegError, log, _subprocess_startup_info, temp_directory, THEMES
from .text import Tokenizer, TypingProfile, TypingAnimator, KeyboardOverlay
from .rendering import CodeRenderer, MultiPaneRenderer
from .audio import SimpleSoundGen, _pcm_to_wav_bytes
import os, math, threading, subprocess, shutil, numpy as np, wave
from PySide6.QtGui import QImage
@dataclass(slots=True)
class ExportConfig:
    junior_code: str; senior_code: str; name: str; fmt: str; res: str; theme: str; j_font: str; s_font: str; font_size_auto: bool; font_size: int; chrome: bool; ln: bool; cursor_glow: bool; watermark: bool; bg_image_path: str; show_kb: bool; kb_layout: str; kb_pos: str; kb_op: float; j_wpm: int; j_pause: float; s_wpm: int; s_pause: float; start_pause: float; end_pause: float; lang: str; pane_layout: str; pad: int; gap: int; target_duration: float = 0.0
class ExportConfigBuilder:
    def __init__(self): self._fields: dict = {}
    def junior_code(self, code: str) -> "ExportConfigBuilder": self._fields["junior_code"] = code; return self
    def senior_code(self, code: str) -> "ExportConfigBuilder": self._fields["senior_code"] = code; return self
    def name(self, n: str) -> "ExportConfigBuilder": self._fields["name"] = n; return self
    def fmt(self, f: str) -> "ExportConfigBuilder": self._fields["fmt"] = f; return self
    def res(self, r: str) -> "ExportConfigBuilder": self._fields["res"] = r; return self
    def theme(self, t: str) -> "ExportConfigBuilder": self._fields["theme"] = t; return self
    def j_font(self, f: str) -> "ExportConfigBuilder": self._fields["j_font"] = f; return self
    def s_font(self, f: str) -> "ExportConfigBuilder": self._fields["s_font"] = f; return self
    def font_size_auto(self, v: bool) -> "ExportConfigBuilder": self._fields["font_size_auto"] = v; return self
    def font_size(self, s: int) -> "ExportConfigBuilder": self._fields["font_size"] = s; return self
    def chrome(self, c: bool) -> "ExportConfigBuilder": self._fields["chrome"] = c; return self
    def ln(self, l: bool) -> "ExportConfigBuilder": self._fields["ln"] = l; return self
    def cursor_glow(self, g: bool) -> "ExportConfigBuilder": self._fields["cursor_glow"] = g; return self
    def watermark(self, w: bool) -> "ExportConfigBuilder": self._fields["watermark"] = w; return self
    def bg_image_path(self, p: str) -> "ExportConfigBuilder": self._fields["bg_image_path"] = p; return self
    def show_kb(self, s: bool) -> "ExportConfigBuilder": self._fields["show_kb"] = s; return self
    def kb_layout(self, l: str) -> "ExportConfigBuilder": self._fields["kb_layout"] = l; return self
    def kb_pos(self, p: str) -> "ExportConfigBuilder": self._fields["kb_pos"] = p; return self
    def kb_op(self, o: float) -> "ExportConfigBuilder": self._fields["kb_op"] = o; return self
    def j_wpm(self, w: int) -> "ExportConfigBuilder": self._fields["j_wpm"] = w; return self
    def j_pause(self, p: float) -> "ExportConfigBuilder": self._fields["j_pause"] = p; return self
    def s_wpm(self, w: int) -> "ExportConfigBuilder": self._fields["s_wpm"] = w; return self
    def s_pause(self, p: float) -> "ExportConfigBuilder": self._fields["s_pause"] = p; return self
    def start_pause(self, s: float) -> "ExportConfigBuilder": self._fields["start_pause"] = s; return self
    def end_pause(self, e: float) -> "ExportConfigBuilder": self._fields["end_pause"] = e; return self
    def lang(self, l: str) -> "ExportConfigBuilder": self._fields["lang"] = l; return self
    def pane_layout(self, p: str) -> "ExportConfigBuilder": self._fields["pane_layout"] = p; return self
    def pad(self, p: int) -> "ExportConfigBuilder": self._fields["pad"] = p; return self
    def gap(self, g: int) -> "ExportConfigBuilder": self._fields["gap"] = g; return self
    def target_duration(self, t: float) -> "ExportConfigBuilder": self._fields["target_duration"] = t; return self
    def build(self) -> ExportConfig: return ExportConfig(**self._fields)
def _build_panes_from_config(cfg: ExportConfig) -> MultiPaneRenderer:
    fmt_data = EXPORT_FORMATS.get(cfg.fmt, EXPORT_FORMATS["YouTube (16:9)"]); w, h = fmt_data["resolutions"].get(cfg.res, (1920, 1080)); theme = THEMES[cfg.theme]
    jp = TypingProfile(name="Junior Dev", wpm=cfg.j_wpm, pause_freq=cfg.j_pause, start_pause=cfg.start_pause, end_pause=cfg.end_pause, error_chance=0.04)
    sp = TypingProfile(name="Senior Dev", wpm=cfg.s_wpm, pause_freq=cfg.s_pause, start_pause=cfg.start_pause, end_pause=cfg.end_pause, error_chance=0.01)
    if cfg.target_duration > 0:
        for profile, code in [(jp, cfg.junior_code), (sp, cfg.senior_code)]:
            req_wpm = TypingAnimator.find_wpm_for_target_duration(code, cfg.target_duration, profile.start_pause, profile.end_pause, profile.error_chance); profile.wpm = max(10, min(500, req_wpm))
    panes: List[Tuple[CodeRenderer, TypingAnimator]] = []
    for i, (code, profile, title, accent_key) in enumerate([(cfg.junior_code, jp, f"👶 Junior Dev — {cfg.name}", "junior_accent"), (cfg.senior_code, sp, f"🧓 Senior Dev — {cfg.name}", "senior_accent")]):
        match cfg.pane_layout:
            case "Side by Side (50/50)": pw, ph = (w - cfg.gap) // 2, h
            case "Stacked (50/50)": pw, ph = w, (h - cfg.gap) // 2
            case "Junior Big (70/30)": pw = int((w - cfg.gap) * 0.7) if i == 0 else (w - cfg.gap) - int((w - cfg.gap) * 0.7); ph = h
            case "Senior Big (70/30)": pw = int((w - cfg.gap) * 0.3) if i == 0 else (w - cfg.gap) - int((w - cfg.gap) * 0.3); ph = h
            case _: pw, ph = (w - cfg.gap) // 2, h
        language = cfg.lang
        if language == "Auto": ext = os.path.splitext(cfg.name)[1].lower(); ext_map = {".py": "Python", ".js": "JavaScript", ".cpp": "CFamily", ".cs": "CSharp"}; language = ext_map.get(ext, "Text")
        code_lines = code.count("\n") + 1; kb_overlay = None; kb_h = 0
        if cfg.show_kb: kb_overlay = KeyboardOverlay(pw, ph, cfg.kb_layout, theme, (ph - 2 * cfg.pad) // 3, cfg.kb_pos, cfg.kb_op); kb_h = kb_overlay.height_needed()
        current_font = cfg.j_font if i == 0 else cfg.s_font
        font_size = (CodeRenderer.auto_font_size(code_lines, pw, ph, cfg.pad, cfg.chrome, cfg.ln, 4, code, current_font, kb_h) if cfg.font_size_auto else max(8, int(cfg.font_size)))
        renderer = CodeRenderer(width=pw, height=ph, theme_name=cfg.theme, font_family=current_font, font_size=font_size, title_text=title, language=language, keyboard_overlay=kb_overlay, padding=cfg.pad, show_window_chrome=cfg.chrome, show_line_numbers=cfg.ln, bg_image_path=cfg.bg_image_path if i == 0 else None, total_code_lines=code_lines, cursor_glow=cfg.cursor_glow, show_watermark=cfg.watermark and i == 0, accent_color=getattr(theme, accent_key, "#7aa2f7"))
        animator = TypingAnimator(code, profile, seed=i * 1000 + 42); panes.append((renderer, animator))
    return MultiPaneRenderer(panes=panes, frame_w=w, frame_h=h, num_panes=2, pane_layout=cfg.pane_layout, gap=cfg.gap)
class VideoExporter(QThread):
    progress = Signal(int); status = Signal(str); finished_ok = Signal(str); error = Signal(str); shorts_ready = Signal(bool, float)
    CHUNK_DURATION: float = 300.0
    def __init__(self, export_config: ExportConfig, output: str, fps: int = 30, sound_params: Optional[Tuple[str, bool, float]] = None, sound_gens: Optional[List[SimpleSoundGen]] = None, volume: float = 0.5, encoder_name: str = "YouTube Pro (H.264/AAC)", bgm_path: Optional[str] = None, bgm_volume: float = 0.3, bgm_duck: float = 0.6, generate_thumbnail: bool = True, generate_chapters: bool = True, parent=None):
        super().__init__(parent); self.setObjectName("VideoExporter"); self.config = export_config; self.output = output; self.fps = fps; self.sound_params = sound_params; self.sound_gens = sound_gens; self.volume = volume; self.encoder_name = encoder_name; self.bgm_path = bgm_path; self.bgm_volume = bgm_volume; self.bgm_duck = bgm_duck; self.generate_thumbnail = generate_thumbnail; self.generate_chapters = generate_chapters; self.multi_pane = _build_panes_from_config(self.config); self._cancel = threading.Event(); self._last_duration = 0.0; self.finished.connect(self.deleteLater)
    def cancel(self) -> None: self._cancel.set()
    def is_cancelled(self) -> bool: return self._cancel.is_set()
    def run(self) -> None:
        audio_executor: Optional[ThreadPoolExecutor] = None
        try:
            anim_dur = self.multi_pane.duration(); self._last_duration = anim_dur; total_dur = self.config.target_duration if self.config.target_duration > 0 else anim_dur; num_chunks = max(1, math.ceil(total_dur / self.CHUNK_DURATION))
            with temp_directory() as tmp_dir:
                audio_executor = ThreadPoolExecutor(max_workers=1) if self.sound_params or self.sound_gens else None; audio_future: Optional[Future] = None; final_audio = os.path.join(tmp_dir, "final_audio.wav")
                if audio_executor:
                    self.status.emit("Starting audio generation...")
                    if self.sound_gens:
                        audio_future = audio_executor.submit(self._gen_full_audio_per_pane, self.multi_pane, final_audio)
                    else:
                        all_ts = self.multi_pane.char_timestamps_all()
                        if all_ts: audio_future = audio_executor.submit(self._gen_full_audio, all_ts, final_audio)
                video_chunks: List[str] = []
                for c_idx in range(num_chunks):
                    if self._cancel.is_set(): break
                    start_t = c_idx * self.CHUNK_DURATION; end_t = min(total_dur, start_t + self.CHUNK_DURATION); start_frame = int(start_t * self.fps); n_chunk = math.ceil(end_t * self.fps) - start_frame
                    self.status.emit(f"Rendering Video Chunk {c_idx + 1}/{num_chunks}..."); chunk_vid = os.path.join(tmp_dir, f"vid_{c_idx}.mp4")
                    if not self._render_chunk_video(start_frame, n_chunk, chunk_vid): raise RenderingError(f"Failed to render video for chunk {c_idx + 1}")
                    video_chunks.append(chunk_vid); self.progress.emit(int(((c_idx + 1) / num_chunks) * 80))
                if self._cancel.is_set(): self.error.emit("Cancelled"); return
                self.status.emit("Stitching video chunks..."); final_video = os.path.join(tmp_dir, "final_video.mp4"); self._concat_files(video_chunks, final_video)
                if audio_future:
                    self.status.emit("Waiting for audio generation to finish..."); audio_ok = audio_future.result()
                    if audio_ok: self.status.emit("Muxing final video and audio..."); self._mux_final(final_video, final_audio, self.output)
                    else: shutil.move(final_video, self.output)
                else: shutil.move(final_video, self.output)
                if self.generate_thumbnail:
                    try: self._generate_thumbnail_candidates(); self.status.emit("Thumbnail candidates saved.")
                    except Exception as e: log.warning("Thumbnail generation failed: %s", e)
                if self.generate_chapters:
                    try: self._generate_chapters_file(); self._generate_description(); self.status.emit("YouTube chapters + description saved.")
                    except Exception as e: log.warning("Chapters/description generation failed: %s", e)
                w, h = self.multi_pane.frame_w, self.multi_pane.frame_h
                self.shorts_ready.emit(h > w and 15 <= anim_dur <= 60, anim_dur)
                self.progress.emit(100); self.status.emit(f"Done → {self.output}"); self.finished_ok.emit(self.output)
        except Exception as e: self.error.emit(str(e))
        finally:
            if audio_executor: audio_executor.shutdown(wait=True)
    def _gen_full_audio(self, ts: List[Tuple[float, str]], filepath: str) -> bool:
        try:
            preset, stereo, reverb_amt = self.sound_params; sg = SimpleSoundGen(sr=44100, preset=preset, stereo=stereo, reverb_amt=reverb_amt); sg.generate_track(ts, filepath, self.volume)
            return os.path.exists(filepath) and os.path.getsize(filepath) > 44
        except Exception: return False
    def _gen_full_audio_per_pane(self, mp: MultiPaneRenderer, filepath: str) -> bool:
        try:
            pcm_tracks = []
            is_stereo = any(g.stereo for g in self.sound_gens if g)
            for pane_idx, (renderer, animator) in enumerate(mp.panes):
                gen = self.sound_gens[pane_idx] if pane_idx < len(self.sound_gens) else self.sound_gens[0]
                if gen:
                    pane_ts = animator.char_timestamps()
                    if pane_ts:
                        pcm = gen.generate_pcm(pane_ts, self.volume)
                        pcm_tracks.append(pcm)
            if not pcm_tracks: return False
            max_len = max(len(t) for t in pcm_tracks)
            if is_stereo:
                mixed_l = np.zeros(max_len // 2, dtype=np.float32); mixed_r = np.zeros(max_len // 2, dtype=np.float32)
                for f in pcm_tracks:
                    if len(f.shape) == 1: mixed_l[:len(f)] += f; mixed_r[:len(f)] += f
                    else: mixed_l[:len(f)//2] += f[0::2]; mixed_r[:len(f)//2] += f[1::2]
                pk = max(float(np.max(np.abs(mixed_l))), float(np.max(np.abs(mixed_r))))
                if pk > 0: mixed_l *= 0.95 / pk; mixed_r *= 0.95 / pk
                out = np.empty(max_len, dtype=np.int16)
                out[0::2] = np.clip(mixed_l * 32767, -32768, 32767).astype(np.int16); out[1::2] = np.clip(mixed_r * 32767, -32768, 32767).astype(np.int16)
            else:
                mixed = np.zeros(max_len, dtype=np.float32)
                for f in pcm_tracks:
                    if len(f.shape) == 1: mixed[:len(f)] += f
                    else: mixed[:len(f)//2] += (f[0::2] + f[1::2]) * 0.5
                pk = float(np.max(np.abs(mixed)))
                if pk > 0: mixed *= 0.95 / pk
                out = np.clip(mixed * 32767, -32768, 32767).astype(np.int16)
            with wave.open(filepath, "w") as wf: wf.setnchannels(2 if is_stereo else 1); wf.setsampwidth(2); wf.setframerate(self.sound_gens[0].sr); wf.writeframes(out.tobytes())
            return os.path.exists(filepath) and os.path.getsize(filepath) > 44
        except Exception as e: log.error("Per-pane audio failed: %s", e); return False
    def _concat_files(self, paths: List[str], out_path: str) -> None:
        concat_path = os.path.join(os.path.dirname(out_path), "concat_list.txt")
        with open(concat_path, "w", encoding="utf-8") as f:
            for p in paths: f.write(f"file '{p}'\n")
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_path, "-c", "copy", out_path]
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, startupinfo=_subprocess_startup_info())
        _, stderr = proc.communicate(timeout=max(120, len(paths) * 60))
        if proc.returncode != 0: raise FFmpegError(f"Concat failed: {stderr.decode(errors='ignore')[-400:]}")
    def _mux_final(self, vid_path: str, aud_path: str, out_path: str) -> None:
        if self.bgm_path and os.path.exists(self.bgm_path):
            sc_ratio = max(1.0, 1.0 + self.bgm_duck * 9.0); sc_threshold = max(0.005, 0.10 - self.bgm_duck * 0.08); sc_makeup = 1.0 + self.bgm_duck * 1.0
            cmd = ["ffmpeg", "-y", "-i", vid_path, "-i", aud_path, "-i", self.bgm_path, "-c:v", "copy", "-filter_complex", f"[2:a]highpass=f=60,lowpass=f=5500,volume={self.bgm_volume},afade=t=in:st=0:d=2[bgm_eq];[bgm_eq][1:a]sidechaincompress=threshold={sc_threshold:.3f}:ratio={sc_ratio:.2f}:attack=0.015:release=0.3:makeup={sc_makeup:.2f}[bgm_duck];[1:a][bgm_duck]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95[aout]", "-map", "0:v", "-map", "[aout]", "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-movflags", "+faststart", out_path]
        else:
            cmd = ["ffmpeg", "-y", "-i", vid_path, "-i", aud_path, "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-movflags", "+faststart", out_path]
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, startupinfo=_subprocess_startup_info())
        _, stderr = proc.communicate(timeout=max(120, os.path.getsize(vid_path) // (1024 * 1024) * 2))
        if proc.returncode != 0: raise FFmpegError(f"Final mux failed: {stderr.decode(errors='ignore')[-400:]}")
    def _render_chunk_video(self, start_frame: int, n_frames: int, out_path: str, allow_fallback: bool = True) -> bool:
        w, h, fps = self.multi_pane.frame_w, self.multi_pane.frame_h, self.fps
        cmd = ["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{w}x{h}", "-r", str(fps), "-i", "pipe:0"]
        enc_cfg = ENCODERS.get(self.encoder_name, ENCODERS["YouTube Pro (H.264/AAC)"]); cmd += enc_cfg["base"]
        if enc_cfg.get("use_dynamic_gop", False): gop = int(fps * 2); cmd += ["-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0"]
        if enc_cfg.get("use_dynamic_vbv", False): pixels = w * h; maxrate = int((pixels / 2073600.0) * 12000000); cmd += ["-maxrate", f"{maxrate}", "-bufsize", f"{maxrate * 2}"]
        if enc_cfg.get("color_tags", False): cmd += ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
        cmd += ["-an", out_path]
        try: proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, startupinfo=_subprocess_startup_info())
        except FileNotFoundError: self.error.emit("FFmpeg not found on PATH."); return False
        scratch = QImage(w, h, QImage.Format_RGB32)
        for fi in range(n_frames):
            if self._cancel.is_set(): break
            t = (start_frame + fi) / self.fps; state = self.multi_pane.compute_state(t); qimg = self.multi_pane.render_frame(t, scratch, precomputed_state=state); ptr = qimg.constBits()
            try: img_bytes = bytes(ptr)
            except TypeError: img_bytes = ptr.tobytes()
            except AttributeError: ptr.setsize(qimg.sizeInBytes()); img_bytes = bytes(ptr)
            try: proc.stdin.write(img_bytes); proc.stdin.flush()
            except BrokenPipeError: return False
        proc.stdin.close(); proc.wait(timeout=600)
        if proc.returncode != 0:
            if allow_fallback and self.encoder_name != "x264 (CPU, Fast)":
                self.encoder_name = "x264 (CPU, Fast)"; self.status.emit("Hardware encoder failed. Retrying with CPU fallback..."); return self._render_chunk_video(start_frame, n_frames, out_path, allow_fallback=False)
            return False
        return True
    def _generate_thumbnail_candidates(self):
        paths = []; base = os.path.splitext(self.output)[0]; vw, vh = self.multi_pane.frame_w, self.multi_pane.frame_h; is_vertical = vh > vw
        tw, th = (1080, 1920) if is_vertical else (1280, 720)
        for i, pct in enumerate([0.25, 0.50, 0.75, 0.95]):
            tp = f"{base}_thumb_{i+1}.jpg"; t = self._last_duration * pct
            state = self.multi_pane.compute_state(t); qimg = self.multi_pane.render_frame(t, None, precomputed_state=state)
            sc = qimg.scaled(tw, th, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            x = (sc.width() - tw) // 2; y = (sc.height() - th) // 2; sc.copy(x, y, tw, th).save(tp, "JPG", 95); paths.append(tp)
        return paths
    def _generate_chapters_file(self):
        cp = os.path.splitext(self.output)[0] + "_chapters.txt"
        markers = Tokenizer.find_structural_markers(self.config.junior_code, self.multi_pane.panes[0][0].language)
        chapters = []
        for co, _, label in markers:
            for ts, idx, ch, _ in self.multi_pane.panes[0][1].timeline:
                if idx == co: chapters.append((ts, label)); break
        chapters = [(0.0, "Intro")] + [c for c in chapters if c[0] > 0]
        if not chapters or chapters[-1][0] < self._last_duration - 5: chapters.append((self._last_duration - 2.0, "Outro"))
        filtered = []; last_ts = -100
        for ts, label in chapters:
            if ts - last_ts >= 10 or ts == 0: filtered.append((ts, label)); last_ts = ts
        def _fmt(s): return f"{int(s//60)}:{int(s%60):02d}" if s < 3600 else f"{int(s//3600)}:{int((s%3600)//60):02d}:{int(s%60):02d}"
        ct = "\n".join(f"{_fmt(ts)} {label}" for ts, label in filtered)
        with open(cp, "w", encoding="utf-8") as f: f.write(ct)
        return cp
    def _generate_description(self):
        dp = os.path.splitext(self.output)[0] + "_description.txt"; title = os.path.splitext(os.path.basename(self.output))[0]; ct = ""
        cp = os.path.splitext(self.output)[0] + "_chapters.txt"
        if os.path.exists(cp):
            with open(cp, "r", encoding="utf-8") as f: ct = f.read()
        desc = f"""{title.replace('_', ' ').title()}

A real-time typing animation of this code, rendered with procedural ASMR audio.

Timestamps:
{ct}

#coding #programming #typingtutorial #code #asmr

---
Generated by Code Typing Studio
Encoder: {self.encoder_name}
Resolution: {self.multi_pane.frame_w}x{self.multi_pane.frame_h}
Duration: {self._last_duration:.1f}s
"""
        with open(dp, "w", encoding="utf-8") as f: f.write(desc)
        return dp
