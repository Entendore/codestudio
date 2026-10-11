from __future__ import annotations
from typing import Dict, List, Tuple, Callable, Optional
from dataclasses import dataclass, field
import io, math, random, wave
import numpy as np
try:
    from scipy.signal import lfilter as _scipy_lfilter, fftconvolve as _scipy_fftconvolve
    _HAS_SCIPY = True
except ImportError: _HAS_SCIPY = False
try:
    import numba
    from numba import njit
    _HAS_NUMBA = True
except ImportError: _HAS_NUMBA = False

if _HAS_NUMBA:
    @njit(cache=True)
    def _nb_k_weight(x, sr):
        n = len(x); y = np.empty(n, dtype=np.float64)
        if sr == 48000: s1_b0, s1_b1, s1_b2, s1_a1, s1_a2 = 1.53512485958624, -2.61187896900947, 1.23925952524423, -1.69019851598276, 0.732626261909496; s2_b0, s2_b1, s2_b2, s2_a1, s2_a2 = 1.0, -2.0, 1.0, -1.99004745481143, 0.990072500365666
        elif sr == 44100: s1_b0, s1_b1, s1_b2, s1_a1, s1_a2 = 1.53058976, -2.60968407, 1.24087861, -1.68589681, 0.73233165; s2_b0, s2_b1, s2_b2, s2_a1, s2_a2 = 1.0, -2.0, 1.0, -1.98993544, 0.98996959
        else:
            for i in range(n): y[i] = x[i]
            return y
        z1 = z2 = 0.0
        for i in range(n): xn=x[i]; yn=s1_b0*xn+z1; z1=s1_b1*xn-s1_a1*yn+z2; z2=s1_b2*xn-s1_a2*yn; y[i]=yn
        z1 = z2 = 0.0
        for i in range(n): xn=y[i]; yn=s2_b0*xn+z1; z1=s2_b1*xn-s2_a1*yn+z2; z2=s2_b2*xn-s2_a2*yn; y[i]=yn
        return y
    @njit(cache=True)
    def _nb_compute_loudness(x_k):
        n = len(x_k)
        if n == 0: return -70.0
        s = 0.0
        for i in range(n): v=x_k[i]; s += v*v
        ms = s / n
        if ms < 1e-20: return -70.0
        return -0.691 + 10.0 * np.log10(ms)
    @njit(cache=True)
    def _nb_apply_gain(x, gain):
        n = len(x); out = np.empty(n, dtype=np.float64)
        for i in range(n): out[i] = x[i] * gain
        return out
    @njit(cache=True)
    def _nb_master_limiter(x, threshold):
        n = len(x); out = np.empty(n, dtype=np.float64); gain = 1.0; rs = 1.0 / 2400.0
        for i in range(n):
            xn = x[i]; ax = xn
            if ax < 0.0: ax = -ax
            if ax * gain > threshold: tg = threshold / max(ax, 1e-10); gain = gain * 0.9 + tg * 0.1
            else: gain = min(1.0, gain + rs)
            out[i] = xn * gain
        return out
    @njit(cache=True)
    def _nb_mix(mix, flat, offsets, lengths, starts):
        n_mix, n_flat = mix.shape[0], flat.shape[0]
        for i in range(len(starts)):
            s, ln, o = starts[i], lengths[i], offsets[i]
            for j in range(ln):
                if s + j < n_mix and o + j < n_flat: mix[s + j] += flat[o + j]

def _k_weight_pure(x, sr):
    if sr == 48000: s1b=[1.53512485958624,-2.61187896900947,1.23925952524423]; s1a=[1.0,-1.69019851598276,0.732626261909496]; s2b=[1.0,-2.0,1.0]; s2a=[1.0,-1.99004745481143,0.990072500365666]
    elif sr == 44100: s1b=[1.53058976,-2.60968407,1.24087861]; s1a=[1.0,-1.68589681,0.73233165]; s2b=[1.0,-2.0,1.0]; s2a=[1.0,-1.98993544,0.98996959]
    else: return x.copy()
    if _HAS_SCIPY:
        from scipy.signal import lfilter
        y = lfilter(s1b, s1a, x); y = lfilter(s2b, s2a, y); return y
    y = np.zeros_like(x); z1 = z2 = 0.0; b0, b1, b2 = s1b; a1, a2 = s1a[1], s1a[2]
    for i in range(len(x)): xn=x[i]; yn=b0*xn+z1; z1=b1*xn-a1*yn+z2; z2=b2*xn-a2*yn; y[i]=yn
    z1 = z2 = 0.0; b0, b1, b2 = s2b; a1, a2 = s2a[1], s2a[2]
    for i in range(len(y)): xn=y[i]; yn=b0*xn+z1; z1=b1*xn-a1*yn+z2; z2=b2*xn-a2*yn; y[i]=yn
    return y

def _compute_lufs(x, sr):
    if len(x) < sr//2: return -70.0
    xf = x.astype(np.float64)
    if _HAS_NUMBA: xk=_nb_k_weight(xf,sr); return float(_nb_compute_loudness(xk))
    xk = _k_weight_pure(xf, sr); ms = np.mean(xk**2)
    if ms < 1e-20: return -70.0
    return -0.691 + 10.0 * np.log10(ms)

def _normalize_to_lufs(x, sr, target_lufs=-14.0, tp_db=-1.0):
    if len(x) < sr//2: return x
    xf = x.astype(np.float64)
    cl = _compute_lufs(xf, sr)
    if not np.isfinite(cl) or cl <= -70.0: return x
    gdb = target_lufs - cl; gl = 10.0 ** (gdb / 20.0)
    if _HAS_NUMBA: xn = _nb_apply_gain(xf, gl)
    else: xn = xf * gl
    tp = 10.0 ** (tp_db / 20.0)
    if _HAS_NUMBA: xn = _nb_master_limiter(xn, tp)
    else:
        p = np.max(np.abs(xn))
        if p > tp: xn = xn * (tp / p)
    return xn

class DSP:
    @staticmethod
    def env_exp(n: int, sr: int, attack: float = 0.001, decay_rate: float = 30.0, attack_curve: float = 2.0) -> np.ndarray:
        t = np.arange(n, dtype=np.float64) / sr; env = np.exp(-t * decay_rate); a_samp = max(1, int(attack * sr))
        if a_samp < n:
            a_env = np.linspace(0, 1, a_samp, dtype=np.float64) ** attack_curve
            env[:a_samp] = a_env * np.exp(-np.arange(a_samp, dtype=np.float64) / sr * decay_rate)
        return env
    @staticmethod
    def fir_lowpass(cutoff: float, sr: int = 44100, n_taps: int = 127, window: str = "blackman") -> np.ndarray:
        if n_taps % 2 == 0: n_taps += 1
        cutoff = min(max(1.0, cutoff), sr / 2.0 - 1.0)
        if cutoff <= 0: return np.zeros(n_taps)
        fc = cutoff / sr; n = np.arange(n_taps, dtype=np.float64); center = (n_taps - 1) / 2.0; h = np.sinc(2 * fc * (n - center))
        match window:
            case "blackman": w = 0.42 - 0.5*np.cos(2*np.pi*n/(n_taps-1)) + 0.08*np.cos(4*np.pi*n/(n_taps-1))
            case "hamming": w = 0.54 - 0.46*np.cos(2*np.pi*n/(n_taps-1))
            case "hann": w = 0.5 - 0.5*np.cos(2*np.pi*n/(n_taps-1))
            case _: w = np.ones(n_taps)
        h *= w; s = np.sum(h)
        if s > 0: h /= s
        return h
    @staticmethod
    def fir_highpass(cutoff: float, sr: int = 44100, n_taps: int = 127, window: str = "blackman") -> np.ndarray:
        hlp = DSP.fir_lowpass(cutoff, sr, n_taps, window); h = -hlp; h[len(h)//2] += 1.0; return h
    @staticmethod
    def fir_bandpass(low: float, high: float, sr: int = 44100, n_taps: int = 255, window: str = "blackman") -> np.ndarray:
        return DSP.fir_lowpass(high, sr, n_taps, window) - DSP.fir_lowpass(low, sr, n_taps, window)
    @staticmethod
    def convolve(x: np.ndarray, h: np.ndarray) -> np.ndarray:
        n, nh = len(x), len(h)
        if nh <= 1: return x * h[0] if nh == 1 else x
        if _HAS_SCIPY and nh > 64: return _scipy_fftconvolve(x, h, mode='full')[:n].astype(np.float32)
        if nh < 64 and n < 2048: return np.convolve(x, h, mode='full')[:n].astype(np.float32)
        MAX_FFT_BITS = 20; total = n + nh - 1
        if total.bit_length() <= MAX_FFT_BITS:
            n_fft = 1 << total.bit_length()
            return np.fft.irfft(np.fft.rfft(x, n_fft) * np.fft.rfft(h, n_fft), n_fft)[:n].astype(np.float32)
        n_fft = 1 << MAX_FFT_BITS; block = n_fft - nh + 1; H = np.fft.rfft(h, n_fft); out = np.zeros(total, dtype=np.float32)
        for i in range(0, n, block):
            end = min(i + block, n); yb = np.fft.irfft(np.fft.rfft(x[i:end], n_fft) * H, n_fft)
            out[i:i + min(len(yb), total - i)] += yb[:min(len(yb), total - i)]
        return out[:n].astype(np.float32)
    @staticmethod
    def filt_lp(x, c, sr=44100, n_taps=127): return DSP.convolve(x, DSP.fir_lowpass(c, sr, n_taps))
    @staticmethod
    def filt_hp(x, c, sr=44100, n_taps=127): return DSP.convolve(x, DSP.fir_highpass(c, sr, n_taps))
    @staticmethod
    def filt_bp(x, l, h, sr=44100, n_taps=255): return DSP.convolve(x, DSP.fir_bandpass(l, h, sr, n_taps))
    @staticmethod
    def filt_resonator(x, f, q, sr=44100, gain=1.0):
        w0 = 2 * np.pi * f / sr; r = np.exp(-w0 / (2 * max(0.5, q))); wd = w0 * np.sqrt(max(0.0, 1.0 - 1.0 / (4 * q * q))) if q > 0.5 else w0
        n_ir = min(len(x), max(16, int(sr * max(0.005, 6.0 * q / max(1.0, f))))); t = np.arange(n_ir, dtype=np.float64); ir = (r ** t) * np.sin(wd * t)
        p = np.max(np.abs(ir))
        if p > 1e-10: ir = ir / p * gain
        return DSP.convolve(x, ir)
    @staticmethod
    def sat_tube(x, drive=1.0):
        if drive <= 0: return x
        return (np.tanh(x * drive) / max(0.001, np.tanh(drive))).astype(np.float32)
    @staticmethod
    def sat_hard(x, t=1.0): return np.clip(x, -t, t).astype(np.float32)
    @staticmethod
    def sat_oversampled(x, drive=1.5):
        if drive <= 0: return x
        n = len(x)
        if n < 2: return DSP.sat_tube(x, drive)
        x_up = np.zeros(n * 2, dtype=np.float32); x_up[0::2] = x; x_up[1:-1:2] = (x[:-1] + x[1:]) / 2.0; x_up[-1] = x[-1]; y_up = DSP.sat_tube(x_up, drive); y_down = (y_up[0:-1:2] + y_up[1::2]) / 2.0
        return y_down[:n].astype(np.float32)
    @staticmethod
    def binaural_pan(mono, pan, sr=44100):
        if abs(pan) < 0.01: return mono.copy(), mono.copy()
        if pan > 0:
            left = DSP.filt_hp(mono, 6000, sr, n_taps=127) * (1.0 - pan * 0.6); right = mono * min(1.0, 1.0 + pan * 0.2)
        else:
            right = DSP.filt_hp(mono, 6000, sr, n_taps=127) * (1.0 + pan * 0.6); left = mono * min(1.0, 1.0 - pan * 0.2)
        itd_samp = int(abs(pan) * 0.0006 * sr); l_out = np.zeros(len(mono) + itd_samp, dtype=np.float32); r_out = np.zeros(len(mono) + itd_samp, dtype=np.float32)
        if pan > 0: l_out[itd_samp:itd_samp + len(left)] += left; r_out[:len(mono)] += right
        else: r_out[itd_samp:itd_samp + len(right)] += right; l_out[:len(mono)] += left
        return l_out, r_out
    @staticmethod
    def compressor_rms(x, sr, threshold_db=-12.0, ratio=3.0, attack_ms=10, release_ms=100):
        if threshold_db >= 0 or ratio <= 1: return x.astype(np.float32)
        target_lin = 10 ** (threshold_db / 20); env = np.abs(x).astype(np.float32); ac = np.exp(-1.0 / (sr * (attack_ms / 1000.0))); rc = np.exp(-1.0 / (sr * (release_ms / 1000.0)))
        if _HAS_SCIPY:
            env_a = _scipy_lfilter([1 - ac], [1, -ac], env); env_r = _scipy_lfilter([1 - rc], [1, -rc], env); smooth_env = np.maximum(env_a, env_r).astype(np.float32)
        else: smooth_env = env
        gain = np.ones_like(x, dtype=np.float32); mask = smooth_env > target_lin
        gain[mask] = 1.0 - (1.0 - 1.0 / ratio) * (1.0 - target_lin / smooth_env[mask])
        return (x * gain).astype(np.float32)
    @staticmethod
    def lookahead_limiter(x, sr=44100, threshold_db=-1.0, release_ms=50):
        target_lin = 10 ** (threshold_db / 20); peak = np.max(np.abs(x))
        if peak <= target_lin: return x.astype(np.float32)
        env = np.abs(x).astype(np.float32); gain = np.ones_like(x, dtype=np.float32); mask = env > target_lin; gain[mask] = target_lin / env[mask]
        lookahead = int(sr * 0.002)
        if lookahead > 0 and len(gain) > lookahead: gain = np.roll(gain, -lookahead); gain[-lookahead:] = gain[-lookahead - 1]
        return (x * gain).astype(np.float32)
    @staticmethod
    def dc_block(x, sr=44100): return DSP.filt_hp(x, 20.0, sr, n_taps=63)
    @staticmethod
    def osc_sine(f, n, sr, phase=0.0): return np.sin(2 * np.pi * f * (np.arange(n, dtype=np.float64) / sr) + phase).astype(np.float32)
    @staticmethod
    def osc_sweep(f1, f2, n, sr):
        if n <= 0: return np.zeros(0, dtype=np.float32)
        if f1 <= 0: f1 = 1.0
        if f2 <= 0: f2 = 1.0
        t = np.arange(n, dtype=np.float64) / sr; freq = f1 * (f2 / f1) ** (t / (n / sr)); return np.sin(2 * np.pi * np.cumsum(freq) / sr).astype(np.float32)
    @staticmethod
    def osc_fm(c, mr, mi, n, sr, phase=0.0):
        t = np.arange(n, dtype=np.float64) / sr; return np.sin(2 * np.pi * c * t + mi * np.sin(2 * np.pi * c * mr * t) + phase).astype(np.float32)
    @staticmethod
    def osc_fm_multi(c, mrs, mis, n, sr):
        t = np.arange(n, dtype=np.float64) / sr; ph = 2 * np.pi * c * t
        for mr, mi in zip(mrs, mis): ph += mi * np.sin(2 * np.pi * c * mr * t)
        return np.sin(ph).astype(np.float32)
    @staticmethod
    def osc_saw(f, n, sr):
        t = np.arange(n, dtype=np.float64) / sr; sig = np.zeros(n, dtype=np.float64)
        for h in range(1, 17):
            fh = f * h
            if fh >= sr * 0.45: break
            sig += np.sin(2 * np.pi * fh * t) / h
        return (sig * 0.5).astype(np.float32)
    @staticmethod
    def noise_white(n, rng): return rng.randn(n).astype(np.float32)
    @staticmethod
    def noise_pink(n, rng, sr=44100):
        bs = min(n, sr * 5); white = rng.randn(bs).astype(np.float32); fft = np.fft.rfft(white); freqs = np.fft.rfftfreq(bs, 1.0 / sr)
        if len(freqs) > 1: freqs[0] = max(freqs[1], 1.0); fft[1:] /= np.sqrt(np.abs(freqs[1:])); fft[0] = 0
        pink = np.fft.irfft(fft, n=bs).astype(np.float32); p = np.max(np.abs(pink))
        if p > 1e-6: pink /= p
        reps, rem = n // bs, n % bs; out = np.tile(pink, reps) if reps > 0 else np.array([], dtype=np.float32)
        if rem > 0: out = np.concatenate([out, pink[:rem]])
        return out
    @staticmethod
    def noise_brown(n, rng):
        white = rng.randn(n).astype(np.float32); brown = np.cumsum(white) - np.mean(np.cumsum(white)); p = np.max(np.abs(brown))
        if p > 0: brown = brown / p
        return (brown * 0.5).astype(np.float32)
    @staticmethod
    def modal(modes, n, sr, inharmonicity=0.0):
        t = np.arange(n, dtype=np.float64) / sr; out = np.zeros(n, dtype=np.float64); f0 = modes[0][0] if modes else 440.0
        for freq, decay, amp in modes:
            af = freq
            if inharmonicity > 0: nh = freq / f0; af = freq * np.sqrt(1 + inharmonicity * nh * nh)
            out += np.sin(2 * np.pi * af * t) * np.exp(-decay * t) * amp
        p = np.max(np.abs(out))
        if p > 1e-6: out /= p
        return out.astype(np.float32)
    @staticmethod
    def normalize(x, target_db=-1.0):
        p = np.max(np.abs(x)) if len(x) > 0 else 0.0
        if p < 1e-10: return x
        return x * (10 ** (target_db / 20) / p)
    @staticmethod
    def to_int16(x, target_db=-1.0):
        x = DSP.normalize(x, target_db); x = DSP.sat_hard(x, 1.0); np.nan_to_num(x, copy=False, nan=0.0, posinf=1.0, neginf=-1.0); return np.clip(x * 32767, -32768, 32767).astype(np.int16)
    @staticmethod
    def normalize_loudness(x, sr, target_lufs=-14.0, true_peak_db=-1.0): return _normalize_to_lufs(x, sr, target_lufs, true_peak_db)
    @staticmethod
    def master_mono(x, sr=44100, target_db=-0.1):
        if len(x) == 0: return x
        x = DSP.dc_block(x, sr); x = DSP.filt_hp(x, 25.0, sr, n_taps=127); sub = DSP.filt_bp(x, 40, 120, sr, n_taps=255); sub = DSP.sat_tube(sub * 1.4, 1.3); x = x + sub * 0.25
        x = x + DSP.filt_lp(x, 150, sr, n_taps=127) * 0.12; x = x - DSP.filt_bp(x, 250, 500, sr, n_taps=255) * 0.12; x = x + DSP.filt_hp(x, 10000, sr, n_taps=63) * 0.20
        x = DSP.compressor_rms(x, sr, threshold_db=-22.0, ratio=1.8, attack_ms=30, release_ms=220); x = DSP.compressor_rms(x, sr, threshold_db=-12.0, ratio=2.5, attack_ms=8, release_ms=100); x = DSP.sat_oversampled(x, drive=1.3)
        return np.clip(DSP.lookahead_limiter(x, sr, threshold_db=target_db), -1.0, 1.0).astype(np.float32)
    @staticmethod
    def master_stereo_asmr(mix_l, mix_r, sr=44100, target_db=-1.0):
        if len(mix_l) == 0: return mix_l, mix_r
        mix_l = DSP.dc_block(mix_l, sr); mix_r = DSP.dc_block(mix_r, sr); mix_l = DSP.filt_hp(mix_l, 25.0, sr, n_taps=127); mix_r = DSP.filt_hp(mix_r, 25.0, sr, n_taps=127)
        sub_l = DSP.filt_bp(mix_l, 40, 120, sr, n_taps=255); sub_r = DSP.filt_bp(mix_r, 40, 120, sr, n_taps=255); sub_l = DSP.sat_tube(sub_l * 1.4, 1.3); sub_r = DSP.sat_tube(sub_r * 1.4, 1.3); mix_l = mix_l + sub_l * 0.25; mix_r = mix_r + sub_r * 0.25
        mid = (mix_l + mix_r) * 0.5; side = (mix_l - mix_r) * 0.5; mid = DSP.filt_lp(mid, 10000, sr, n_taps=127); side = side * 1.12; mix_l = mid + side; mix_r = mid - side
        ess_l = DSP.filt_bp(mix_l, 5000, 8500, sr, n_taps=255); ess_r = DSP.filt_bp(mix_r, 5000, 8500, sr, n_taps=255); ess_env_l = np.abs(ess_l).astype(np.float32); ess_env_r = np.abs(ess_r).astype(np.float32); ess_thresh = 0.12
        ess_gain_l = np.where(ess_env_l > ess_thresh, ess_thresh / np.maximum(ess_env_l, 1e-10), 1.0).astype(np.float32); ess_gain_r = np.where(ess_env_r > ess_thresh, ess_thresh / np.maximum(ess_env_r, 1e-10), 1.0).astype(np.float32)
        if _HAS_SCIPY: ess_gain_l = _scipy_lfilter([1.0], [1.0, -0.99], ess_gain_l).astype(np.float32); ess_gain_r = _scipy_lfilter([1.0], [1.0, -0.99], ess_gain_r).astype(np.float32)
        mix_l = mix_l - ess_l * (1.0 - ess_gain_l) * 0.4; mix_r = mix_r - ess_r * (1.0 - ess_gain_r) * 0.4
        mix_l = DSP.sat_tube(mix_l, drive=1.25); mix_r = DSP.sat_tube(mix_r, drive=1.25)
        mix_l = DSP.compressor_rms(mix_l, sr, threshold_db=-24.0, ratio=1.6, attack_ms=40, release_ms=250); mix_r = DSP.compressor_rms(mix_r, sr, threshold_db=-24.0, ratio=1.6, attack_ms=40, release_ms=250)
        mix_l = DSP.compressor_rms(mix_l, sr, threshold_db=-12.0, ratio=2.5, attack_ms=8, release_ms=100); mix_r = DSP.compressor_rms(mix_r, sr, threshold_db=-12.0, ratio=2.5, attack_ms=8, release_ms=100)
        air_l = DSP.filt_hp(mix_l, 11000, sr, n_taps=63); air_r = DSP.filt_hp(mix_r, 11000, sr, n_taps=63); air_l = DSP.sat_tube(air_l, 1.8) * 0.5; air_r = DSP.sat_tube(air_r, 1.8) * 0.5; mix_l = mix_l + air_l * 0.15; mix_r = mix_r + air_r * 0.15
        mix_l = DSP.lookahead_limiter(mix_l, sr, threshold_db=target_db, release_ms=80); mix_r = DSP.lookahead_limiter(mix_r, sr, threshold_db=target_db, release_ms=80)
        return (np.clip(mix_l, -1.0, 1.0).astype(np.float32), np.clip(mix_r, -1.0, 1.0).astype(np.float32))

@dataclass(slots=True)
class Voice:
    osc: str = "sine"; freq: float = 440.0; freq_end: float = 0.0; fm_ratio: float = 1.0; fm_index: float = 0.0; fm_ratios: Optional[List[float]] = None; fm_indices: Optional[List[float]] = None
    attack: float = 0.001; decay_rate: float = 30.0; attack_curve: float = 2.0; filt: str = "none"; f_cutoff: float = 8000.0; f_high: float = 12000.0; f_q: float = 1.0; drive: float = 0.0; gain: float = 0.5; delay: float = 0.0
    modes: Optional[List[Tuple[float, float, float]]] = None; inharmonicity: float = 0.0

class SoundRenderer:
    __slots__ = ("sr",)
    def __init__(self, sr: int = 44100): self.sr = sr
    def render(self, voices: List[Voice], duration: float, rng: np.random.RandomState) -> np.ndarray:
        n = int(self.sr * duration)
        if n <= 0: return np.zeros(0, dtype=np.float32)
        mix = np.zeros(n, dtype=np.float32)
        for v in voices:
            sig = self._render_voice(v, n, rng); d_samp = int(v.delay * self.sr)
            if d_samp > 0:
                if d_samp >= n: continue
                sig = np.concatenate([np.zeros(d_samp, dtype=np.float32), sig[:n - d_samp]])
            if len(sig) < n: sig = np.concatenate([sig, np.zeros(n - len(sig), dtype=np.float32)])
            else: sig = sig[:n]
            mix += sig
        return DSP.dc_block(mix, self.sr)
    def _render_voice(self, v: Voice, n: int, rng: np.random.RandomState) -> np.ndarray:
        match v.osc:
            case "sine": sig = DSP.osc_sine(v.freq, n, self.sr)
            case "sweep": sig = DSP.osc_sweep(v.freq, v.freq_end, n, self.sr)
            case "fm": sig = DSP.osc_fm_multi(v.freq, v.fm_ratios, v.fm_indices, n, self.sr) if v.fm_ratios else DSP.osc_fm(v.freq, v.fm_ratio, v.fm_index, n, self.sr)
            case "noise_white": sig = DSP.noise_white(n, rng)
            case "noise_pink": sig = DSP.noise_pink(n, rng, self.sr)
            case "noise_brown": sig = DSP.noise_brown(n, rng)
            case "modal": sig = DSP.modal(v.modes, n, self.sr, v.inharmonicity) if v.modes else np.zeros(n, dtype=np.float32)
            case "saw": sig = DSP.osc_saw(v.freq, n, self.sr)
            case _: sig = np.zeros(n, dtype=np.float32)
        sig *= DSP.env_exp(n, self.sr, attack=v.attack, decay_rate=v.decay_rate, attack_curve=v.attack_curve)
        match v.filt:
            case "lp": sig = DSP.filt_lp(sig, v.f_cutoff, self.sr)
            case "hp": sig = DSP.filt_hp(sig, v.f_cutoff, self.sr)
            case "bp": sig = DSP.filt_bp(sig, v.f_cutoff, v.f_high, self.sr)
            case "resonator": sig = DSP.filt_resonator(sig, v.f_cutoff, v.f_q, self.sr)
        if v.drive > 0: sig = DSP.sat_tube(sig, v.drive)
        return sig * v.gain

class VoiceFactory:
    @staticmethod
    def click(freq, gain=0.5, delay=0.0, rng=None):
        if rng: freq = freq * (2 ** rng.uniform(-0.12, 0.12))
        return Voice(osc="sweep", freq=freq * 1.8, freq_end=freq * 0.9, attack=0.0001, decay_rate=350.0, attack_curve=3.0, filt="bp", f_cutoff=freq * 0.8, f_high=freq * 1.8, drive=2.0, gain=gain, delay=delay)
    @staticmethod
    def fm(freq, fm_idx=0.4, gain=0.35, cutoff=3500, delay=0.0, rng=None):
        if rng: freq = freq * (2 ** rng.uniform(-0.08, 0.08))
        return Voice(osc="fm", freq=freq, fm_ratio=2.0, fm_index=fm_idx, attack=0.0003, decay_rate=80.0, attack_curve=2.0, filt="lp", f_cutoff=cutoff, drive=1.2, gain=gain, delay=delay)
    @staticmethod
    def housing(freq, gain=0.30, delay=0.0, rng=None):
        if rng: freq = freq * (2 ** rng.uniform(-0.06, 0.06))
        return Voice(osc="modal", modes=[(freq, 25.0, 1.0), (freq * 1.8, 40.0, 0.4), (freq * 2.5, 60.0, 0.2)], attack=0.0008, decay_rate=0.0, attack_curve=1.5, filt="lp", f_cutoff=2500, gain=gain, delay=delay)
    @staticmethod
    def thock(freq, gain=0.45, delay=0.002, rng=None):
        if rng: freq = freq * (2 ** rng.uniform(-0.10, 0.10))
        return Voice(osc="sweep", freq=freq * 1.3, freq_end=freq * 0.85, attack=0.0015, decay_rate=45.0, attack_curve=2.5, filt="lp", f_cutoff=max(500.0, freq * 4.0), gain=gain, delay=delay)
    @staticmethod
    def sub(freq, gain=0.30, delay=0.003, rng=None):
        if rng: freq = freq * (2 ** rng.uniform(-0.05, 0.05))
        return Voice(osc="sine", freq=freq, attack=0.002, decay_rate=30.0, attack_curve=2.0, filt="lp", f_cutoff=250, gain=gain, delay=delay)
    @staticmethod
    def rattle(lo, hi, gain=0.08, delay=0.002): return Voice(osc="noise_white", attack=0.0005, decay_rate=100.0, attack_curve=1.0, filt="bp", f_cutoff=lo, f_high=hi, drive=1.5, gain=gain, delay=delay)
_VF = VoiceFactory

SOUND_PRESETS: Dict[str, Dict[str, str]] = {
    "Mechanical": {"desc": "Cherry MX Blue: sharp click jacket, tactile bump, deep housing resonance"},
    "ASMR Deep Thock": {"desc": "Ultra-deep sub-bass thock with soft pink noise, optimized for brain tingles"},
    "Topre Thock": {"desc": "Electrostatic capacitive switch: deep, rubbery sub-bass thock with muted tactile bump."},
    "Holy Panda": {"desc": "Custom tactile switch: sharp, forceful tactile bump with a hollow housing resonance."},
    "Cherry MX Black": {"desc": "Heavy linear switch: smooth, consistent force with a dense, deep thock."},
    "Studio ASMR": {"desc": "Broadcast-ready typing with EQ matching, transient shaping, and convolution reverb."},
    "Cinematic Impact": {"desc": "Deep sub-bass impacts and huge transient punch, designed for dramatic coding montages."},
    "Heavenly ASMR": {"desc": "Angellic, wide-stereo modal chimes paired with soft sub-bass. Processed with tape saturation."},
    "Lo-Fi Chill": {"desc": "Warm, heavily tape-saturated and low-passed keys perfect for relaxing coding sessions."},
    "Cyberpunk Hacker": {"desc": "Ring-modulated metallic transients and heavy sub-bass impacts. Sci-fi UI sound design."},
    "Ethereal Shimmer": {"desc": "Soft modal plucks processed through a massive pitch-shifted shimmer reverb tail."},
    "Vocal Whisper": {"desc": "Formant-filtered to sound like a soft human whisper. Creepy, intimate, and highly ASMR-inducing."},
    "Vintage Tape": {"desc": "Authentic analog tape emulation with pitch wow/flutter, heavy saturation, and glue compression."},
    "Retro Arcade": {"desc": "True 4-bit bitcrushed square waves and noise bursts. Authentic 80s arcade cabinet sound design."},
    "Granular Sci-Fi": {"desc": "Granular pitch-shifted FM synthesis for complex, digital artifact-heavy futuristic UI sounds."},
    "Psychedelic": {"desc": "Heavy stereo chorus and tape saturation for a liquid, sweeping, 1960s psychedelic texture."},
    "Cinematic Boom": {"desc": "Sub-harmonic synthesis and transient design for massive, blockbuster trailer-style impacts."},
    "Studio Plate": {"desc": "Classic 4-comb/2-allpass algorithmic plate reverb for lush, 1980s studio ambiance."},
    "Neuro Bass": {"desc": "Aggressive FM growl, 8-bit mid crunch, and massive sub-harmonic bass. Perfect for EDM production."},
    "Aphex Exciter": {"desc": "Harmonic exciter pushes high-frequency presence to the absolute limit. Crisp, airy, and hyper-detailed."},
    "80s Synthwave": {"desc": "Phase distortion synthesis and dynamic notch filtering for authentic 1980s digital keyboard textures."},
    "Deep Ocean": {"desc": "Massive sub-harmonic bass and pitch-shifted shimmer reverb tails for abyssal, underwater atmospheres."},
    "Glass Shatter": {"desc": "Extreme transient design on high-frequency modal synthesis for hyper-realistic, sharp glass impacts."},
    "Anechoic Chamber": {"desc": "Surgical parametric EQ removes all room resonance and mud. The driest, most clinical sound possible."},
    "Gated Room": {"desc": "Massive 80s gated-reverb effect. Huge explosive impact that cuts off instantly. Perfect for dramatic emphasis."},
    "Foley Crunch": {"desc": "Granularly synthesized pink and white noise particles simulating stepping on gravel, leaves, or debris."},
    "Alien Artifact": {"desc": "Linear frequency shifting and ring modulation create unnatural, metallic, sci-fi UI soundscapes."},
    "Phase Sweep": {"desc": "All-pass filter phase shifter creates a sweeping, 1970s psychedelic comb filter effect."},
    "Metal Pipe": {"desc": "Convolved with a synthesized metallic pipe IR for massive, resonant cinematic clangs and impacts."},
    "Analog Tube": {"desc": "Chebyshev polynomial waveshaping generates warm, even harmonics characteristic of vintage tube amplifiers."},
    "FM Radio": {"desc": "Aggressive multiband compression, 15kHz bandwidth limitation, and pre-emphasis EQ for authentic broadcast sound."},
    "Acid Bassline": {"desc": "Resonant state-variable filter and heavy distortion create the classic squelching TB-303 acid house sound."},
    "West-Coast Synth": {"desc": "Analog wavefolding distortion and state-variable filtering create complex, metallic Buchla-style textures."},
    "Tape Echo": {"desc": "Analog tape delay with pitch wow/flutter in the feedback loop for warm, degrading echoes."},
    "Vocoder Bot": {"desc": "Cross-synthesis imposes the spectral envelope of a keystroke onto a saw wave, creating a robotic, talking effect."},
    "Modular Deep Thock": {"desc": "Fully parameterized deep thock with heavy sub-bass and minimal transients."},
    "Modular Crystal Chime": {"desc": "Parameterized inharmonic bell with modal synthesis."}
}
CLICK_DURATIONS: Dict[str, float] = {k: 0.12 for k in SOUND_PRESETS.keys()}
CLICK_DURATIONS["ASMR Deep Thock"] = 0.18; CLICK_DURATIONS["Crystal"] = 0.40; CLICK_DURATIONS["Ethereal Shimmer"] = 0.40

def _mk_click(sr, dur, seed, voices):
    rng = np.random.RandomState(seed); R = SoundRenderer(sr)
    return DSP.to_int16(R.render(voices, dur, rng))

_PRESET_FACTORIES: Dict[str, Dict[str, Callable]] = {
    "Mechanical": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.click(4800*(2**np.random.RandomState(s).uniform(-0.1,0.1)),0.55,rng=np.random.RandomState(s)),_VF.fm(1400,0.45,0.35,4000,rng=np.random.RandomState(s)),_VF.housing(420,0.35,rng=np.random.RandomState(s)),_VF.thock(160,0.50,0.002,rng=np.random.RandomState(s)),_VF.sub(85,0.35,0.003,rng=np.random.RandomState(s)),_VF.rattle(2200,4500,0.06,0.002)]), "space": lambda sr,d,s: _mk_click(sr,0.1,s,[_VF.click(3500,0.40,rng=np.random.RandomState(s)),_VF.thock(120,0.50,0.002,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.1,s,[_VF.click(3000,0.45,rng=np.random.RandomState(s)),_VF.thock(100,0.55,0.002,rng=np.random.RandomState(s))])},
    "ASMR Deep Thock": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.thock(110,0.75,0.001,rng=np.random.RandomState(s)),_VF.sub(60,0.55,0.002,rng=np.random.RandomState(s)),Voice(osc="noise_pink",attack=0.006,decay_rate=45.0,filt="lp",f_cutoff=900,gain=0.18),_VF.housing(240,0.22,0.003,rng=np.random.RandomState(s))]), "space": lambda sr,d,s: _mk_click(sr,0.14,s,[_VF.thock(85,0.80,0.001,rng=np.random.RandomState(s)),_VF.sub(45,0.60,0.002,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.18,s,[_VF.thock(65,0.85,0.001,rng=np.random.RandomState(s)),_VF.sub(35,0.65,0.002,rng=np.random.RandomState(s))])},
    "Topre Thock": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.thock(150,0.50,0.002,rng=np.random.RandomState(s)),_VF.sub(80,0.45,0.001,rng=np.random.RandomState(s)),Voice(osc="noise_white",attack=0.0005,decay_rate=220.0,filt="lp",f_cutoff=1500,gain=0.10)]), "space": lambda sr,d,s: _mk_click(sr,0.12,s,[_VF.thock(110,0.50,0.002,rng=np.random.RandomState(s)),_VF.sub(60,0.50,0.001,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.11,s,[_VF.thock(120,0.55,0.002,rng=np.random.RandomState(s)),_VF.sub(65,0.55,0.001,rng=np.random.RandomState(s))])},
    "Holy Panda": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.click(2000,0.20,rng=np.random.RandomState(s)),_VF.fm(350,0.35,0.35,1500,rng=np.random.RandomState(s)),_VF.thock(110,0.45,0.0005,rng=np.random.RandomState(s)),_VF.sub(70,0.30,0.001,rng=np.random.RandomState(s))]), "space": lambda sr,d,s: _mk_click(sr,0.1,s,[_VF.fm(250,0.35,0.35,1200,rng=np.random.RandomState(s)),_VF.thock(90,0.50,0.0005,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.09,s,[_VF.fm(200,0.40,0.40,1000,rng=np.random.RandomState(s)),_VF.thock(80,0.55,0.0005,rng=np.random.RandomState(s))])},
    "Cherry MX Black": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.thock(180,0.45,0.0012,rng=np.random.RandomState(s)),_VF.sub(100,0.50,0.0015,rng=np.random.RandomState(s)),_VF.sub(60,0.35,0.002,rng=np.random.RandomState(s))]), "space": lambda sr,d,s: _mk_click(sr,0.1,s,[_VF.thock(120,0.50,0.0012,rng=np.random.RandomState(s)),_VF.sub(70,0.55,0.0015,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.09,s,[_VF.thock(100,0.55,0.0012,rng=np.random.RandomState(s)),_VF.sub(55,0.60,0.0015,rng=np.random.RandomState(s))])},
    "Studio ASMR": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.thock(220,0.45,0.001,rng=np.random.RandomState(s)),_VF.sub(140,0.50,0.001,rng=np.random.RandomState(s)),Voice(osc="noise_white",attack=0.0003,decay_rate=250.0,filt="bp",f_cutoff=2500,f_high=5000,gain=0.15)]), "space": lambda sr,d,s: _mk_click(sr,0.11,s,[_VF.thock(160,0.50,0.001,rng=np.random.RandomState(s)),_VF.sub(100,0.55,0.001,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.14,s,[_VF.thock(140,0.55,0.001,rng=np.random.RandomState(s)),_VF.sub(90,0.60,0.001,rng=np.random.RandomState(s))])},
    "Cinematic Impact": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.sub(60,0.60,0.0,rng=np.random.RandomState(s)),_VF.thock(220,0.40,0.001,rng=np.random.RandomState(s)),Voice(osc="noise_white",attack=0.0002,decay_rate=400.0,filt="bp",f_cutoff=3000,f_high=6000,gain=0.15,delay=0.001)]), "space": lambda sr,d,s: _mk_click(sr,0.16,s,[_VF.sub(50,0.60,0.0,rng=np.random.RandomState(s)),_VF.thock(160,0.45,0.001,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.3,s,[_VF.sub(45,0.70,0.0,rng=np.random.RandomState(s)),_VF.thock(120,0.55,0.001,rng=np.random.RandomState(s))])},
    "Heavenly ASMR": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.housing(800,0.40,0.002,rng=np.random.RandomState(s)),_VF.sub(100,0.45,0.001,rng=np.random.RandomState(s)),Voice(osc="modal",modes=[(800,12,1.0),(1600,10,0.5),(2800,8,0.3)],attack=0.002,decay_rate=0.0,filt="lp",f_cutoff=8000,gain=0.40)]), "space": lambda sr,d,s: _mk_click(sr,0.16,s,[_VF.housing(500,0.35,0.002,rng=np.random.RandomState(s)),_VF.sub(70,0.55,0.001,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.22,s,[_VF.housing(600,0.35,0.002,rng=np.random.RandomState(s)),_VF.sub(60,0.60,0.001,rng=np.random.RandomState(s))])},
    "Lo-Fi Chill": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.thock(150,0.55,0.004,rng=np.random.RandomState(s)),_VF.sub(100,0.25,0.001,rng=np.random.RandomState(s)),Voice(osc="noise_pink",attack=0.005,decay_rate=15.0,filt="lp",f_cutoff=500,gain=0.25,delay=0.001)]), "space": lambda sr,d,s: _mk_click(sr,0.14,s,[_VF.thock(100,0.60,0.004,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.18,s,[_VF.thock(80,0.65,0.004,rng=np.random.RandomState(s))])},
    "Cyberpunk Hacker": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.click(3000,0.25,rng=np.random.RandomState(s)),_VF.fm(800,1.5,0.30,4000,rng=np.random.RandomState(s)),_VF.sub(80,0.50,0.001,rng=np.random.RandomState(s))]), "space": lambda sr,d,s: _mk_click(sr,0.09,s,[_VF.fm(600,1.2,0.25,3500,rng=np.random.RandomState(s)),_VF.sub(60,0.55,0.001,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.12,s,[_VF.fm(400,2.0,0.25,3000,rng=np.random.RandomState(s)),_VF.sub(50,0.60,0.001,rng=np.random.RandomState(s))])},
    "Ethereal Shimmer": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.housing(500,0.45,0.005,rng=np.random.RandomState(s)),Voice(osc="noise_pink",attack=0.002,decay_rate=80.0,filt="hp",f_cutoff=2000,gain=0.10,delay=0.001)]), "space": lambda sr,d,s: _mk_click(sr,0.35,s,[_VF.housing(350,0.40,0.005,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.6,s,[_VF.housing(450,0.42,0.005,rng=np.random.RandomState(s))])},
    "Vocal Whisper": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.housing(120,0.40,0.002,rng=np.random.RandomState(s)),Voice(osc="noise_pink",attack=0.002,decay_rate=90.0,filt="lp",f_cutoff=2000,gain=0.30)]), "space": lambda sr,d,s: _mk_click(sr,0.13,s,[_VF.housing(90,0.45,0.002,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.14,s,[_VF.housing(80,0.50,0.002,rng=np.random.RandomState(s))])},
    "Vintage Tape": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.thock(180,0.55,0.003,rng=np.random.RandomState(s)),Voice(osc="noise_white",attack=0.001,decay_rate=100.0,filt="lp",f_cutoff=1500,gain=0.25)]), "space": lambda sr,d,s: _mk_click(sr,0.11,s,[_VF.thock(120,0.60,0.003,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.12,s,[_VF.thock(100,0.65,0.003,rng=np.random.RandomState(s))])},
    "Retro Arcade": {"click": lambda sr,d,s: _mk_click(sr,d,s,[Voice(osc="saw",freq=200,attack=0.001,decay_rate=60.0,filt="lp",f_cutoff=1500,gain=0.40),Voice(osc="noise_white",attack=0.0005,decay_rate=150.0,filt="hp",f_cutoff=1000,gain=0.20)]), "space": lambda sr,d,s: _mk_click(sr,0.07,s,[Voice(osc="saw",freq=150,attack=0.001,decay_rate=50.0,filt="lp",f_cutoff=1200,gain=0.45)]), "enter": lambda sr,d,s: _mk_click(sr,0.09,s,[Voice(osc="saw",freq=100,attack=0.001,decay_rate=40.0,filt="lp",f_cutoff=1000,gain=0.50)])},
    "Granular Sci-Fi": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.fm(600,1.5,0.35,4000,rng=np.random.RandomState(s)),_VF.sub(80,0.40,0.001,rng=np.random.RandomState(s))]), "space": lambda sr,d,s: _mk_click(sr,0.14,s,[_VF.fm(400,1.2,0.30,3500,rng=np.random.RandomState(s)),_VF.sub(60,0.45,0.001,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.18,s,[_VF.fm(500,2.0,0.30,3000,rng=np.random.RandomState(s)),_VF.sub(50,0.50,0.001,rng=np.random.RandomState(s))])},
    "Psychedelic": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.fm(400,0.8,0.35,4000,rng=np.random.RandomState(s)),Voice(osc="noise_pink",attack=0.002,decay_rate=60.0,filt="lp",f_cutoff=1500,gain=0.15)]), "space": lambda sr,d,s: _mk_click(sr,0.18,s,[_VF.fm(300,0.8,0.30,3000,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.25,s,[_VF.fm(350,0.8,0.35,2500,rng=np.random.RandomState(s))])},
    "Cinematic Boom": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.sub(60,0.40,0.0,rng=np.random.RandomState(s)),_VF.thock(180,0.30,0.001,rng=np.random.RandomState(s))]), "space": lambda sr,d,s: _mk_click(sr,0.2,s,[_VF.sub(50,0.45,0.0,rng=np.random.RandomState(s)),_VF.thock(120,0.35,0.001,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.3,s,[_VF.sub(45,0.50,0.0,rng=np.random.RandomState(s)),_VF.thock(100,0.40,0.001,rng=np.random.RandomState(s))])},
    "Studio Plate": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.thock(220,0.45,0.001,rng=np.random.RandomState(s)),Voice(osc="noise_white",attack=0.0003,decay_rate=300.0,filt="bp",f_cutoff=2500,f_high=5000,gain=0.15)]), "space": lambda sr,d,s: _mk_click(sr,0.11,s,[_VF.thock(160,0.50,0.001,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.14,s,[_VF.thock(140,0.55,0.001,rng=np.random.RandomState(s))])},
    "Neuro Bass": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.fm(150,1.2,0.40,800,rng=np.random.RandomState(s)),_VF.sub(50,0.40,0.0,rng=np.random.RandomState(s))]), "space": lambda sr,d,s: _mk_click(sr,0.14,s,[_VF.fm(120,1.0,0.45,600,rng=np.random.RandomState(s)),_VF.sub(45,0.45,0.0,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.18,s,[_VF.fm(100,0.8,0.50,500,rng=np.random.RandomState(s)),_VF.sub(40,0.50,0.0,rng=np.random.RandomState(s))])},
    "Aphex Exciter": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.thock(200,0.45,0.001,rng=np.random.RandomState(s)),Voice(osc="noise_white",attack=0.0003,decay_rate=300.0,filt="hp",f_cutoff=3000,gain=0.15)]), "space": lambda sr,d,s: _mk_click(sr,0.11,s,[_VF.thock(150,0.50,0.001,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.14,s,[_VF.thock(130,0.55,0.001,rng=np.random.RandomState(s))])},
    "80s Synthwave": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.fm(300,0.8,0.40,1500,rng=np.random.RandomState(s)),_VF.sub(75,0.50,0.001,rng=np.random.RandomState(s))]), "space": lambda sr,d,s: _mk_click(sr,0.14,s,[_VF.fm(200,0.8,0.45,1200,rng=np.random.RandomState(s)),_VF.sub(50,0.55,0.001,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.18,s,[_VF.fm(150,0.8,0.50,1000,rng=np.random.RandomState(s)),_VF.sub(40,0.60,0.001,rng=np.random.RandomState(s))])},
    "Deep Ocean": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.sub(50,0.60,0.01,rng=np.random.RandomState(s)),Voice(osc="noise_pink",attack=0.05,decay_rate=5.0,filt="lp",f_cutoff=300,gain=0.20,delay=0.005)]), "space": lambda sr,d,s: _mk_click(sr,0.25,s,[_VF.sub(40,0.65,0.01,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.4,s,[_VF.sub(35,0.70,0.015,rng=np.random.RandomState(s))])},
    "Glass Shatter": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.housing(3000,0.40,0.0005,rng=np.random.RandomState(s)),Voice(osc="noise_white",attack=0.0001,decay_rate=600.0,filt="hp",f_cutoff=4000,gain=0.25)]), "space": lambda sr,d,s: _mk_click(sr,0.14,s,[_VF.housing(2000,0.45,0.0005,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.2,s,[_VF.housing(2500,0.40,0.0005,rng=np.random.RandomState(s))])},
    "Anechoic Chamber": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.thock(220,0.45,0.001,rng=np.random.RandomState(s)),Voice(osc="noise_white",attack=0.0003,decay_rate=350.0,filt="bp",f_cutoff=3000,f_high=6000,gain=0.18)]), "space": lambda sr,d,s: _mk_click(sr,0.09,s,[_VF.thock(160,0.50,0.001,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.11,s,[_VF.thock(140,0.55,0.001,rng=np.random.RandomState(s))])},
    "Gated Room": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.click(1500,0.40,rng=np.random.RandomState(s)),_VF.thock(180,0.30,0.001,rng=np.random.RandomState(s))]), "space": lambda sr,d,s: _mk_click(sr,0.18,s,[_VF.click(1200,0.35,rng=np.random.RandomState(s)),_VF.thock(120,0.35,0.001,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.22,s,[_VF.click(1000,0.30,rng=np.random.RandomState(s)),_VF.thock(100,0.40,0.001,rng=np.random.RandomState(s))])},
    "Foley Crunch": {"click": lambda sr,d,s: _mk_click(sr,d,s,[Voice(osc="noise_pink",attack=0.001,decay_rate=150.0,filt="lp",f_cutoff=2000,gain=0.35),Voice(osc="noise_white",attack=0.0005,decay_rate=200.0,filt="hp",f_cutoff=4000,gain=0.15)]), "space": lambda sr,d,s: _mk_click(sr,0.07,s,[Voice(osc="noise_pink",attack=0.001,decay_rate=130.0,filt="lp",f_cutoff=1500,gain=0.30)]), "enter": lambda sr,d,s: _mk_click(sr,0.09,s,[Voice(osc="noise_pink",attack=0.001,decay_rate=120.0,filt="lp",f_cutoff=1200,gain=0.25)])},
    "Alien Artifact": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.fm(300,1.0,0.45,3000,rng=np.random.RandomState(s)),Voice(osc="noise_white",attack=0.0002,decay_rate=400.0,filt="bp",f_cutoff=2000,f_high=5000,gain=0.15)]), "space": lambda sr,d,s: _mk_click(sr,0.11,s,[_VF.fm(200,0.8,0.40,2500,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.14,s,[_VF.fm(250,1.5,0.45,2000,rng=np.random.RandomState(s))])},
    "Phase Sweep": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.thock(200,0.45,0.001,rng=np.random.RandomState(s)),Voice(osc="noise_white",attack=0.0003,decay_rate=300.0,filt="bp",f_cutoff=2500,f_high=5000,gain=0.15)]), "space": lambda sr,d,s: _mk_click(sr,0.13,s,[_VF.thock(150,0.50,0.001,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.16,s,[_VF.thock(130,0.55,0.001,rng=np.random.RandomState(s))])},
    "Metal Pipe": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.click(1500,0.35,rng=np.random.RandomState(s)),_VF.thock(150,0.30,0.001,rng=np.random.RandomState(s))]), "space": lambda sr,d,s: _mk_click(sr,0.25,s,[_VF.click(1200,0.30,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.35,s,[_VF.click(1000,0.28,rng=np.random.RandomState(s))])},
    "Analog Tube": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.thock(180,0.45,0.002,rng=np.random.RandomState(s)),Voice(osc="noise_white",attack=0.0004,decay_rate=250.0,filt="bp",f_cutoff=2500,f_high=5000,gain=0.12)]), "space": lambda sr,d,s: _mk_click(sr,0.12,s,[_VF.thock(130,0.50,0.002,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.14,s,[_VF.thock(110,0.55,0.002,rng=np.random.RandomState(s))])},
    "FM Radio": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.thock(200,0.40,0.001,rng=np.random.RandomState(s)),Voice(osc="noise_white",attack=0.0003,decay_rate=300.0,filt="bp",f_cutoff=2500,f_high=5000,gain=0.15)]), "space": lambda sr,d,s: _mk_click(sr,0.09,s,[_VF.thock(150,0.45,0.001,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.11,s,[_VF.thock(130,0.50,0.001,rng=np.random.RandomState(s))])},
    "Acid Bassline": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.fm(150,1.2,0.50,800,rng=np.random.RandomState(s))]), "space": lambda sr,d,s: _mk_click(sr,0.11,s,[_VF.fm(100,1.0,0.55,600,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.14,s,[_VF.fm(80,0.8,0.60,500,rng=np.random.RandomState(s))])},
    "West-Coast Synth": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.fm(300,0.5,0.45,3000,rng=np.random.RandomState(s))]), "space": lambda sr,d,s: _mk_click(sr,0.11,s,[_VF.fm(200,0.5,0.50,2500,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.14,s,[_VF.fm(150,0.8,0.55,2000,rng=np.random.RandomState(s))])},
    "Tape Echo": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.thock(200,0.45,0.001,rng=np.random.RandomState(s)),Voice(osc="noise_white",attack=0.0003,decay_rate=350.0,filt="bp",f_cutoff=2500,f_high=5000,gain=0.15)]), "space": lambda sr,d,s: _mk_click(sr,0.09,s,[_VF.thock(150,0.50,0.001,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.11,s,[_VF.thock(130,0.55,0.001,rng=np.random.RandomState(s))])},
    "Vocoder Bot": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.fm(120,0.5,0.40,1500,rng=np.random.RandomState(s)),Voice(osc="noise_pink",attack=0.002,decay_rate=90.0,filt="lp",f_cutoff=2000,gain=0.30)]), "space": lambda sr,d,s: _mk_click(sr,0.14,s,[_VF.fm(90,0.5,0.45,1200,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.16,s,[_VF.fm(80,0.5,0.50,1000,rng=np.random.RandomState(s))])},
    "Modular Deep Thock": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.thock(120,0.55,0.003,rng=np.random.RandomState(s)),_VF.sub(60,0.50,0.001,rng=np.random.RandomState(s)),Voice(osc="noise_white",attack=0.0005,decay_rate=200.0,filt="lp",f_cutoff=1000,gain=0.08)]), "space": lambda sr,d,s: _mk_click(sr,0.13,s,[_VF.thock(90,0.55,0.003,rng=np.random.RandomState(s)),_VF.sub(50,0.55,0.001,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.12,s,[_VF.thock(100,0.60,0.003,rng=np.random.RandomState(s)),_VF.sub(55,0.60,0.001,rng=np.random.RandomState(s))])},
    "Modular Crystal Chime": {"click": lambda sr,d,s: _mk_click(sr,d,s,[_VF.housing(1500,0.50,0.0001,rng=np.random.RandomState(s)),Voice(osc="noise_white",attack=0.0001,decay_rate=400.0,filt="bp",f_cutoff=1000,f_high=2000,gain=0.15)]), "space": lambda sr,d,s: _mk_click(sr,0.25,s,[_VF.housing(900,0.50,0.0001,rng=np.random.RandomState(s))]), "enter": lambda sr,d,s: _mk_click(sr,0.7,s,[_VF.housing(2200,0.45,0.0001,rng=np.random.RandomState(s))])}
}

class SimpleSoundGen:
    _SHIFT_MAP = {'~': '`', '!': '1', '@': '2', '#': '3', '$': '4', '%': '5', '^': '6', '&': '7', '*': '8', '(': '9', ')': '0', '_': '-', '+': '=', '{': '[', '}': ']', '|': '\\', ':': ';', '"': "'", '<': ',', '>': '.', '?': '/'}
    def __init__(self, sr: int = 44100, preset: str = "Mechanical", stereo: bool = False, reverb_amt: float = 0.1, target_lufs: float = -14.0):
        self.sr, self.preset, self.stereo, self.reverb_amt, self.target_lufs = sr, preset, stereo, reverb_amt, target_lufs
        factories = _PRESET_FACTORIES.get(preset, _PRESET_FACTORIES["Mechanical"]); click_dur = CLICK_DURATIONS.get(preset, 0.06)
        self.key_sounds = {}; seed_base = 0
        for ch in "`1234567890-=[]\\": snd = factories["click"](sr, click_dur, seed_base); self.key_sounds[ch.lower()] = self._pitch_shift(snd, 1.10, sr); seed_base += 1
        for ch in "qwertyuiop": snd = factories["click"](sr, click_dur, seed_base); self.key_sounds[ch.lower()] = self._pitch_shift(snd, 1.04, sr); seed_base += 1
        for ch in "asdfghjkl;'": snd = factories["click"](sr, click_dur, seed_base); self.key_sounds[ch.lower()] = self._pitch_shift(snd, 0.98, sr); seed_base += 1
        for ch in "zxcvbnm,./": snd = factories["click"](sr, click_dur, seed_base); self.key_sounds[ch.lower()] = self._pitch_shift(snd, 0.92, sr); seed_base += 1
        self.mod_sounds = {}; mod_seed = 500
        for mod_name in ["tab", "caps", "shift", "ctrl", "alt", "win", "fn", "menu", "esc"]: snd = factories["space"](sr, mod_seed); self.mod_sounds[mod_name] = self._pitch_shift(snd, 0.88, sr); mod_seed += 1
        self.fallback_clicks = [factories["click"](sr, click_dur, 900 + i) for i in range(8)]
        self.spaces = [self._pitch_shift(factories["space"](sr, 600 + i), 0.85, sr) for i in range(3)]
        self.enters = [self._pitch_shift(factories["enter"](sr, 700 + i), 0.86, sr) for i in range(2)]
        self.releases = [self._make_release_tick(sr, 300 + i) for i in range(8)]
        self._ir_l = self._ir_r = None
        if self.reverb_amt > 0: self._get_reverb_ir()
    @staticmethod
    def _pitch_shift(sample: np.ndarray, mult: float, sr: int = 44100) -> np.ndarray:
        if mult == 1.0 or len(sample) == 0: return sample
        s = sample.astype(np.float32) / 32767.0
        if mult > 1.0: s = DSP.filt_lp(s, min(8500, (sr / 2.0 / mult) * 0.9), sr, n_taps=63)
        n = len(s); new_n = max(1, int(n / mult)); indices = np.linspace(0, n - 1, new_n); left = np.floor(indices).astype(int); right = np.minimum(left + 1, n - 1); frac = indices - left; shifted = s[left] * (1 - frac) + s[right] * frac
        if mult < 1.0: shifted = DSP.filt_hp(shifted, 30.0, sr, n_taps=63); sub = DSP.filt_bp(shifted, 40, 120, sr, n_taps=127); shifted = shifted + sub * 0.25
        return np.clip(shifted * 32767, -32768, 32767).astype(np.int16)
    def _make_release_tick(self, sr=44100, seed=0) -> np.ndarray:
        rng = np.random.RandomState(seed); R = SoundRenderer(sr); v = [Voice(osc="noise_white", attack=0.0001, decay_rate=600.0, attack_curve=2.0, filt="hp", f_cutoff=5000, drive=1.5, gain=0.18)]; return DSP.to_int16(R.render(v, 0.035, rng))
    def _get_reverb_ir(self) -> Tuple[np.ndarray, np.ndarray]:
        if self._ir_l is None:
            sr = self.sr; n = int(sr * 0.8); rng = np.random.RandomState(42); t = np.arange(n, dtype=np.float32) / sr; ir_l, ir_r = np.zeros(n, dtype=np.float32), np.zeros(n, dtype=np.float32)
            for i, (dt, g) in enumerate(zip(np.linspace(0.005, 0.080, 40), np.linspace(0.8, 0.1, 40))):
                ds = int(dt * sr)
                if ds < n:
                    if i % 2 == 0: ir_l[ds] += g * (1.0 if rng.rand() > 0.5 else -1.0); ir_r[min(ds + int(0.002 * sr), n - 1)] += g * 0.5 * (1.0 if rng.rand() > 0.5 else -1.0)
                    else: ir_r[ds] += g * (1.0 if rng.rand() > 0.5 else -1.0); ir_l[min(ds + int(0.0015 * sr), n - 1)] += g * 0.5 * (1.0 if rng.rand() > 0.5 else -1.0)
            if np.max(np.abs(ir_l)) > 0: ir_l /= np.max(np.abs(ir_l))
            if np.max(np.abs(ir_r)) > 0: ir_r /= np.max(np.abs(ir_r))
            self._ir_l, self._ir_r = DSP.filt_hp(DSP.filt_lp(ir_l, 7000, sr, n_taps=127), 150, sr, n_taps=63), DSP.filt_hp(DSP.filt_lp(ir_r, 7000, sr, n_taps=127), 150, sr, n_taps=63)
        return self._ir_l, self._ir_r
    def _pick(self, char: str, rng: random.Random) -> Tuple[np.ndarray, float]:
        if char == "__release__": return rng.choice(self.releases), rng.uniform(0.4, 0.6)
        if char == "\n": return rng.choice(self.enters), rng.uniform(1.10, 1.25)
        if char == " ": return self.spaces[rng.randint(0, 2)], rng.uniform(1.05, 1.15)
        if char == "\t": return self.mod_sounds["tab"], rng.uniform(0.95, 1.05)
        cl = char.lower()
        if cl in self._SHIFT_MAP: cl = self._SHIFT_MAP[cl]
        if cl in self.key_sounds: return self.key_sounds[cl], rng.uniform(0.92, 1.08)
        return rng.choice(self.fallback_clicks), rng.uniform(0.92, 1.08)
    def generate_pcm(self, timestamps: List[Tuple[float, str]], volume: float = 0.5) -> np.ndarray:
        if not timestamps: return np.zeros(0, dtype=np.int16)
        sr = self.sr; rng = random.Random(98765); all_ts: List[Tuple[float, str]] = []
        for ts, ch in timestamps:
            all_ts.append((ts, ch))
            if ch.isalnum() or ch in ".,;[]'": all_ts.append((ts + 0.045 + rng.uniform(0, 0.015), "__release__"))
        n = int(sr * (max(ts for ts, _ in all_ts) + 0.3)); raw_sounds: List[Tuple[np.ndarray, float, int]] = []
        for ts, ch in all_ts:
            snd, vm = self._pick(ch, rng); s = int(ts * sr)
            if s >= n: continue
            snd_float = snd.astype(np.float32) / 32767.0
            if len(snd_float) == 0: continue
            snd_mono = snd_float * ((rng.gauss(1.0, 0.05) * vm) ** 0.8) * volume; pan = 0.0
            if self.stereo:
                cl = ch.lower()
                if cl in self._SHIFT_MAP: cl = self._SHIFT_MAP[cl]
                if cl in "qazwsx12345edcrfv tgbyhn": pan = rng.uniform(-0.7, -0.3)
                elif cl in "yp[]\\=-7890uio phjklnm,./;'": pan = rng.uniform(0.3, 0.7)
                elif ch in (" ", "\n", "\t"): pan = rng.uniform(-0.1, 0.1)
                else: pan = rng.uniform(-0.3, 0.3)
            raw_sounds.append((snd_mono, pan, s))
        if not raw_sounds: return np.zeros(n, dtype=np.int16)
        if self.stereo:
            max_len = n + int(0.001 * sr); mix_l, mix_r = np.zeros(max_len, dtype=np.float32), np.zeros(max_len, dtype=np.float32)
            for snd_mono, pan, s in raw_sounds:
                l_sig, r_sig = DSP.binaural_pan(snd_mono, pan, sr); e = min(s + len(l_sig), max_len); mix_l[s:e] += l_sig[:e - s]; mix_r[s:e] += r_sig[:e - s]
            if self.reverb_amt > 0:
                ir_l, ir_r = self._get_reverb_ir(); mix_l = mix_l * (1.0 - self.reverb_amt) + DSP.convolve(mix_l, ir_l)[:max_len] * self.reverb_amt; mix_r = mix_r * (1.0 - self.reverb_amt) + DSP.convolve(mix_r, ir_r)[:max_len] * self.reverb_amt
            mix_l, mix_r = mix_l[:n], mix_r[:n]; mix_l, mix_r = DSP.master_stereo_asmr(mix_l, mix_r, sr)
            # LUFS Normalization
            combined = (mix_l + mix_r) * 0.5
            cl = _compute_lufs(combined, sr)
            if np.isfinite(cl) and cl > -70:
                gdb = self.target_lufs - cl; gl = 10.0 ** (gdb / 20.0); mix_l *= gl; mix_r *= gl
            mix_l = DSP.lookahead_limiter(mix_l, sr, threshold_db=-1.0); mix_r = DSP.lookahead_limiter(mix_r, sr, threshold_db=-1.0)
            out = np.empty(n * 2, dtype=np.int16)
            out[0::2] = np.clip(mix_l * 32767, -32768, 32767).astype(np.int16); out[1::2] = np.clip(mix_r * 32767, -32768, 32767).astype(np.int16); return out
        mono_sounds = [s[0] for s in raw_sounds]; starts = np.array([s[2] for s in raw_sounds], dtype=np.int64); lengths = np.array([len(s) for s in mono_sounds], dtype=np.int64); flat = np.concatenate(mono_sounds) if mono_sounds else np.array([], dtype=np.float32); offsets = np.zeros(len(mono_sounds), dtype=np.int64)
        for i in range(1, len(mono_sounds)): offsets[i] = offsets[i - 1] + lengths[i - 1]
        mix = np.zeros(n, dtype=np.float32)
        if len(flat) > 0:
            if _HAS_NUMBA: _nb_mix(mix, flat, offsets, lengths, starts)
            else:
                for i in range(len(starts)): s_idx, ln, o = int(starts[i]), int(lengths[i]), int(offsets[i]); mix[s_idx:s_idx + ln] += flat[o:o + ln]
        if self.reverb_amt > 0:
            ir_l, ir_r = self._get_reverb_ir(); mix = mix * (1.0 - self.reverb_amt) + DSP.convolve(mix, (ir_l + ir_r) * 0.5)[:n] * self.reverb_amt
        mix = DSP.master_mono(mix, sr)
        mix = DSP.normalize_loudness(mix, sr, self.target_lufs)
        return np.clip(mix * 32767.0, -32768, 32767).astype(np.int16)
    def generate_track(self, timestamps: List[Tuple[float, str]], filepath: str, volume: float = 0.5) -> None:
        if not timestamps: return
        pcm = self.generate_pcm(timestamps, volume)
        if len(pcm) == 0: return
        with wave.open(filepath, "w") as wf: wf.setnchannels(2 if self.stereo else 1); wf.setsampwidth(2); wf.setframerate(self.sr); wf.writeframes(pcm.astype('<i2').tobytes())

def _pcm_to_wav_bytes(pcm: np.ndarray, sr: int = 44100, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "w") as wf: wf.setnchannels(channels); wf.setsampwidth(2); wf.setframerate(sr); wf.writeframes(pcm.tobytes())
    return buf.getvalue()
