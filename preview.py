from __future__ import annotations
from typing import Optional
from PySide6.QtCore import Qt, QTimer, QElapsedTimer, QUrl, QBuffer, QIODevice, Signal, QObject
from PySide6.QtGui import QImage, QPixmap, QColor
from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout, QVBoxLayout, QPushButton, QSlider, QSizePolicy, QGraphicsDropShadowEffect
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from .core import log
from .rendering import MultiPaneRenderer
from .audio import SimpleSoundGen, _pcm_to_wav_bytes
class PreviewImageLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent); self._pixmap: Optional[QPixmap] = None; self.setAlignment(Qt.AlignCenter); self.setMinimumSize(480, 270); self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding); self.setStyleSheet("background: #11111b; border-radius: 8px;")
    def set_preview_image(self, qimg: QImage) -> None: self._pixmap = QPixmap.fromImage(qimg); self._update_painted()
    def resizeEvent(self, event) -> None: super().resizeEvent(event); self._update_painted()
    def _update_painted(self) -> None:
        if self._pixmap is None: return
        scaled = self._pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation); self.setPixmap(scaled)
class PreviewController(QObject):
    state_changed = Signal(float, float); audio_failed = Signal(str)
    def __init__(self, parent=None):
        super().__init__(parent); self.multi_pane: Optional[MultiPaneRenderer] = None; self.scratch_image: Optional[QImage] = None; self.sound_gen: Optional[SimpleSoundGen] = None; self.audio_volume: float = 0.5; self.progress: float = 0.0; self.anim_t: float = 0.0; self.animating: bool = False; self.loop: bool = False; self.target_duration: float = 0.0
        self._timer = QTimer(self); self._timer.setTimerType(Qt.PreciseTimer); self._timer.setInterval(16); self._timer.timeout.connect(self._advance); self._clock = QElapsedTimer(); self._audio_player: Optional[QMediaPlayer] = None; self._audio_out: Optional[QAudioOutput] = None; self._audio_buf: Optional[QBuffer] = None
    def attach_audio(self, player: QMediaPlayer, output: QAudioOutput) -> None: self._audio_player = player; self._audio_out = output
    def set_renderer(self, mp: MultiPaneRenderer, scratch: Optional[QImage] = None) -> None: self.multi_pane = mp; self.scratch_image = scratch or QImage(mp.frame_w, mp.frame_h, QImage.Format_RGB32)
    def set_sound(self, sg: Optional[SimpleSoundGen], volume: float = 0.5) -> None: self.sound_gen = sg; self.audio_volume = max(0.0, min(1.0, volume))
    def total_duration(self) -> float:
        if self.multi_pane is None: return 0.0
        if self.target_duration > 0: return self.target_duration
        return self.multi_pane.duration()
    def play(self) -> None:
        if self.multi_pane is None: return
        total = self.total_duration()
        if self.progress >= 0.999: self.progress, self.anim_t = 0.0, 0.0
        else: self.anim_t = total * self.progress
        self.animating = True; self._clock.start(); self._maybe_start_audio(); self._timer.start()
    def pause(self) -> None: self.animating = False; self._timer.stop(); self._stop_audio()
    def toggle(self) -> bool:
        if self.animating: self.pause(); return False
        self.play(); return True
    def seek(self, progress: float) -> None: self.pause(); self.progress = max(0.0, min(1.0, progress)); self.anim_t = self.total_duration() * self.progress; self.state_changed.emit(self.anim_t, self.total_duration())
    def seek_delta(self, delta_seconds: float) -> None: total = self.total_duration(); new_t = max(0.0, min(total, total * self.progress + delta_seconds)); self.anim_t = new_t; self.progress = new_t / total if total > 0 else 0.0; self.state_changed.emit(self.anim_t, total)
    def reset(self) -> None: self.pause(); self.progress = 0.0; self.anim_t = 0.0; self.state_changed.emit(0.0, self.total_duration())
    def set_loop(self, enabled: bool) -> None: self.loop = enabled
    def render_frame_at(self, progress: float) -> Optional[QImage]:
        if self.multi_pane is None: return None
        total = self.total_duration(); t = min(progress * total, self.multi_pane.duration()); state = self.multi_pane.compute_state(t); return self.multi_pane.render_frame(t, self.scratch_image, precomputed_state=state)
    def _advance(self) -> None:
        dt = min(self._clock.restart() / 1000.0, 0.1); self.anim_t += dt; total = self.total_duration()
        if self.anim_t >= total:
            if self.loop: self.anim_t = 0.0
            else: self.anim_t = total; self.pause()
        self.progress = min(1.0, self.anim_t / total) if total > 0 else 0.0; self.state_changed.emit(self.anim_t, total)
    def _maybe_start_audio(self) -> None:
        if self.sound_gen is None or self._audio_player is None: return
        try:
            ts = self.multi_pane.char_timestamps_all() if self.multi_pane else []
            if not ts: return
            pcm = self.sound_gen.generate_pcm(ts, self.audio_volume)
            if len(pcm) == 0: return
            channels = 2 if self.sound_gen.stereo else 1; wav_bytes = _pcm_to_wav_bytes(pcm, self.sound_gen.sr, channels); self._stop_audio(); self._audio_buf = QBuffer(); self._audio_buf.setData(wav_bytes); self._audio_buf.open(QIODevice.ReadOnly); self._audio_out.setVolume(self.audio_volume); self._audio_player.setSourceDevice(self._audio_buf, QUrl()); self._audio_player.play()
        except Exception as e: log.warning("Preview audio failed: %s", e); self.audio_failed.emit(str(e))
    def _stop_audio(self) -> None:
        if self._audio_player is not None: self._audio_player.stop(); self._audio_player.setSource(QUrl())
        if self._audio_buf is not None:
            try: self._audio_buf.close()
            except Exception: pass
            self._audio_buf = None
class PreviewWidget(QWidget):
    progress_changed = Signal(float); play_toggled = Signal(bool)
    def __init__(self, parent=None):
        super().__init__(parent); self.controller = PreviewController(self); self._build_ui(); self.controller.state_changed.connect(self._on_state_changed); self.controller.audio_failed.connect(lambda e: log.warning("Preview audio: %s", e))
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self); layout.setContentsMargins(12, 12, 12, 12); layout.setSpacing(8)
        self.image_label = PreviewImageLabel(); shadow = QGraphicsDropShadowEffect(); shadow.setBlurRadius(20); shadow.setOffset(0, 4); shadow.setColor(QColor(0, 0, 0, 120)); self.image_label.setGraphicsEffect(shadow); layout.addWidget(self.image_label, 1)
        stats = QHBoxLayout(); self.time_label = QLabel("0:00 / 0:00"); self.time_label.setStyleSheet("color: #a6e3a1; font-size: 12px; font-weight: bold; font-family: monospace;"); stats.addWidget(self.time_label)
        self.stats_label = QLabel(""); self.stats_label.setStyleSheet("color: #a6adc8; font-size: 11px; font-family: monospace;"); stats.addWidget(self.stats_label); stats.addStretch()
        self.pct_label = QLabel("0%"); self.pct_label.setStyleSheet("color: #89b4fa; font-size: 11px; font-weight: bold; font-family: monospace;"); stats.addWidget(self.pct_label); layout.addLayout(stats)
        ctrl = QHBoxLayout()
        self.frame_back_btn = QPushButton("⏮"); self.frame_back_btn.setFixedWidth(40); self.frame_back_btn.clicked.connect(lambda: self.controller.seek_delta(-1 / 30)); ctrl.addWidget(self.frame_back_btn)
        self.slider = QSlider(Qt.Horizontal); self.slider.setRange(0, 1000); self.slider.valueChanged.connect(self._on_slider); ctrl.addWidget(self.slider, 1)
        self.frame_fwd_btn = QPushButton("⏭"); self.frame_fwd_btn.setFixedWidth(40); self.frame_fwd_btn.clicked.connect(lambda: self.controller.seek_delta(1 / 30)); ctrl.addWidget(self.frame_fwd_btn)
        self.loop_btn = QPushButton("🔁"); self.loop_btn.setFixedWidth(40); self.loop_btn.setCheckable(True); self.loop_btn.toggled.connect(self.controller.set_loop); ctrl.addWidget(self.loop_btn)
        self.play_btn = QPushButton("▶ Animate"); self.play_btn.setFixedWidth(110); self.play_btn.setStyleSheet("QPushButton { background: #a6e3a1; color: #1e1e2e; font-weight: bold; border: none; border-radius: 6px; padding: 6px; } QPushButton:hover { background: #94e2d5; }"); self.play_btn.clicked.connect(self._toggle_play); ctrl.addWidget(self.play_btn)
        layout.addLayout(ctrl)
        help_label = QLabel("Space: Play/Pause  ·  ←/→: Seek  ·  L: Loop  ·  R: Reset"); help_label.setStyleSheet("color: #585b70; font-size: 10px;"); help_label.setAlignment(Qt.AlignCenter); layout.addWidget(help_label)
    def set_multi_pane(self, mp: MultiPaneRenderer, sound_gen: Optional[SimpleSoundGen] = None, volume: float = 0.5) -> None: self.controller.set_renderer(mp); self.controller.set_sound(sound_gen, volume); self.controller.reset(); self._render_current()
    def attach_audio(self, player: QMediaPlayer, output: QAudioOutput) -> None: self.controller.attach_audio(player, output)
    def set_target_duration(self, seconds: float) -> None: self.controller.target_duration = max(0.0, seconds); self.controller.reset(); self._render_current()
    def stop(self) -> None: self.controller.pause()
    def _toggle_play(self) -> None: playing = self.controller.toggle(); self.play_btn.setText("⏸ Pause" if playing else "▶ Animate"); self.play_toggled.emit(playing)
    def _on_slider(self, value: int) -> None: self.controller.seek(value / 1000.0); self._render_current()
    def _on_state_changed(self, t: float, total: float) -> None:
        self._render_current(); cur_m, cur_s = divmod(int(t), 60); tot_m, tot_s = divmod(int(total), 60); self.time_label.setText(f"{cur_m}:{cur_s:02d} / {tot_m}:{tot_s:02d}"); self.pct_label.Text(f"{int(self.controller.progress * 100)}%")
        self.slider.blockSignals(True); self.slider.setValue(int(self.controller.progress * 1000)); self.slider.blockSignals(False); self.progress_changed.emit(self.controller.progress)
    def _render_current(self) -> None:
        qimg = self.controller.render_frame_at(self.controller.progress)
        if qimg is not None: self.image_label.set_preview_image(qimg)
