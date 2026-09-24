"""会议录音模块。

Windows 上要同时录到"会议对方的声音"和"自己的声音"，必须分别取两路音频：
  - 系统音频：通过 WASAPI loopback 直接抓取声卡回放流，与具体会议软件无关，
    因此腾讯会议、Zoom、Teams、浏览器网页会议都能覆盖。
  - 麦克风：走默认输入设备。

两路音频的采样率通常不同（常见 48 kHz 与 44.1 kHz），模块内部统一重采样为
16 kHz 单声道，并以增量方式写入 WAV，避免长会议把音频堆在内存里 ——
本机可用内存仅 2.5 GB，两小时会议若全内存缓存会产生数百 MB 占用。

【重要设计约束】PortAudio 的初始化和设备打开不是线程安全的。
早期版本为每一路音频各起一个线程、各自 new 一个 PyAudio 实例并并发打开流，
在实测中直接触发段错误（SIGSEGV）。因此现在改为：
  - 整个进程只创建一个 PyAudio 实例；
  - 所有流在主线程打开；
  - 主线程以小帧长轮流读取两路流（每帧约 21 ms，远小于设备缓冲区容量）。

依赖：pyaudiowpatch（PyAudio 的 WASAPI loopback 分支）、numpy。均为可选依赖，
缺失时本模块给出安装指引而不影响转写与纪要功能。
"""

from __future__ import annotations

import time
import wave
from pathlib import Path

from .config import Config

TARGET_RATE = 16000
FRAMES_PER_BUFFER = 1024


class RecorderError(RuntimeError):
    pass


def _import_audio():
    try:
        import pyaudiowpatch as pyaudio  # type: ignore
    except ImportError:
        try:
            import pyaudio  # type: ignore
        except ImportError:
            raise RecorderError(
                "录音功能需要 pyaudiowpatch，请执行：\n"
                "  pip install pyaudiowpatch numpy\n"
                "（pyaudiowpatch 是 PyAudio 的 Windows WASAPI loopback 分支，"
                "普通 pyaudio 无法录制系统声音）"
            ) from None
        return pyaudio, False
    return pyaudio, True


def _import_numpy():
    try:
        import numpy as np  # type: ignore

        return np
    except ImportError:
        raise RecorderError("录音功能需要 numpy，请执行：pip install numpy") from None


def check_recording_ready() -> tuple[bool, str]:
    """供自检命令使用，不打开任何设备。"""
    try:
        pyaudio, loopback_capable = _import_audio()
    except RecorderError as exc:
        return False, str(exc).splitlines()[0]
    try:
        _import_numpy()
    except RecorderError as exc:
        return False, str(exc).splitlines()[0]
    if not loopback_capable:
        return False, "已安装 pyaudio，但缺少 WASAPI loopback 支持（需改用 pyaudiowpatch），无法录制系统音频"
    p = None
    try:
        p = pyaudio.PyAudio()
        p.get_host_api_info_by_type(pyaudio.paWASAPI)
    except Exception as exc:
        return False, f"WASAPI 不可用：{exc}"
    finally:
        if p is not None:
            p.terminate()
    return True, "依赖就绪，可录制系统音频与麦克风"


def list_devices() -> str:
    pyaudio, loopback_capable = _import_audio()
    p = pyaudio.PyAudio()
    lines: list[str] = []
    try:
        try:
            wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
            lines.append(f"WASAPI 主机索引：{wasapi['index']}")
            lines.append(f"默认输出设备：{wasapi.get('defaultOutputDevice', '?')}")
        except Exception:
            lines.append("WASAPI 主机未找到（可能不是 Windows，或音频驱动异常）")

        lines.append("")
        lines.append("全部设备：")
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            flags = []
            if info.get("maxInputChannels", 0) > 0:
                flags.append("输入")
            if info.get("maxOutputChannels", 0) > 0:
                flags.append("输出")
            if info.get("isLoopbackDevice"):
                flags.append("loopback")
            lines.append(
                f"  [{i:>2}] {info.get('name')} "
                f"({int(info.get('defaultSampleRate', 0))} Hz, {int(info.get('maxInputChannels', 0))} ch) "
                f"{'/'.join(flags)}"
            )
        if not loopback_capable:
            lines.append("")
            lines.append("提示：当前使用的是普通 pyaudio，未提供 loopback 设备列表。")
            lines.append("如需录制系统音频，请安装 pyaudiowpatch：pip install pyaudiowpatch")
    finally:
        p.terminate()
    return "\n".join(lines)


# ---------------------------------------------------------------- 重采样


def to_16k_mono(data: bytes, src_rate: int, channels: int):
    """把原始 PCM 数据转为 16 kHz 单声道 int16 数组。

    Python 3.13 已移除 audioop，因此这里用 numpy 自行实现：
    整数倍降采样先做移动平均抗混叠再抽取，非整数倍用线性插值。
    """
    np = _import_numpy()
    arr = np.frombuffer(data, dtype=np.int16)
    if arr.size == 0:
        return arr

    if channels > 1:
        usable = (arr.size // channels) * channels
        arr = arr[:usable].reshape(-1, channels).astype(np.float32).mean(axis=1)
    else:
        arr = arr.astype(np.float32)

    if src_rate != TARGET_RATE and arr.size > 0:
        if src_rate % TARGET_RATE == 0:
            factor = src_rate // TARGET_RATE
            pad = (-arr.size) % factor
            if pad:
                arr = np.concatenate([arr, np.zeros(pad, dtype=np.float32)])
            arr = arr.reshape(-1, factor).mean(axis=1)
        else:
            n_out = int(round(arr.size * TARGET_RATE / src_rate))
            if n_out <= 0:
                return np.zeros(0, dtype=np.int16)
            idx = np.linspace(0, arr.size - 1, n_out)
            arr = np.interp(idx, np.arange(arr.size), arr)

    return np.clip(arr, -32768, 32767).astype(np.int16)


# ---------------------------------------------------------------- 设备定位


def _find_loopback_device(pyaudio_mod, p) -> int:
    """定位默认输出设备对应的 loopback 设备索引。"""
    try:
        wasapi = p.get_host_api_info_by_type(pyaudio_mod.paWASAPI)
    except Exception as exc:
        raise RecorderError(
            f"未找到 WASAPI 主机接口：{exc}\n"
            f"录制系统音频需要 Windows + pyaudiowpatch。"
        ) from exc

    default_index = wasapi.get("defaultOutputDevice")
    if default_index is None:
        raise RecorderError("系统未设置默认输出设备，无法捕获系统音频。")

    default_speaker = p.get_device_info_by_index(int(default_index))
    if default_speaker.get("isLoopbackDevice"):
        return int(default_index)

    target_name = str(default_speaker.get("name") or "")
    for loopback in p.get_loopback_device_info_generator():
        if target_name and target_name in str(loopback.get("name") or ""):
            return int(loopback["index"])

    for loopback in p.get_loopback_device_info_generator():
        return int(loopback["index"])

    raise RecorderError(
        "未找到可用的 loopback 设备。请确认已安装 pyaudiowpatch，"
        "且系统输出设备处于启用状态。"
    )


def _find_microphone(pyaudio_mod, p) -> int | None:
    try:
        return int(p.get_default_input_device_info()["index"])
    except Exception:
        return None


# ---------------------------------------------------------------- 录音器


class _Source:
    """一路已打开的输入流。"""

    __slots__ = ("label", "stream", "rate", "channels", "overflow")

    def __init__(self, label: str, stream, rate: int, channels: int):
        self.label = label
        self.stream = stream
        self.rate = rate
        self.channels = channels
        self.overflow = 0


class MeetingRecorder:
    """同时采集系统音频与麦克风，增量写入 16 kHz 单声道 WAV。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.sample_rate = int(cfg.get("recording", "sample_rate", default=TARGET_RATE))
        self.capture_system = bool(cfg.get("recording", "capture_system_audio", default=True))
        self.capture_mic = bool(cfg.get("recording", "capture_microphone", default=True))
        self.chunk_seconds = int(cfg.get("recording", "chunk_seconds", default=10))
        self._pa = None
        self._sources: list[_Source] = []
        self._wave: wave.Wave_write | None = None
        self._output: Path | None = None

    # ---------- 生命周期 ----------

    def _open_source(self, pyaudio_mod, index: int, label: str) -> _Source:
        info = self._pa.get_device_info_by_index(index)
        rate = int(info["defaultSampleRate"])
        channels = max(1, min(2, int(info.get("maxInputChannels") or 1)))
        stream = self._pa.open(
            format=pyaudio_mod.paInt16,
            channels=channels,
            rate=rate,
            frames_per_buffer=FRAMES_PER_BUFFER,
            input=True,
            input_device_index=index,
            start=False,
        )
        stream.start_stream()
        return _Source(f"{label}[{info.get('name')}]", stream, rate, channels)

    def start(self, output: Path) -> Path:
        pyaudio, loopback_capable = _import_audio()
        _import_numpy()

        if self.capture_system and not loopback_capable:
            raise RecorderError(
                "配置要求录制系统音频，但当前安装的是普通 pyaudio。\n"
                "请执行 pip install pyaudiowpatch，或把 config.yaml 中的 "
                "recording.capture_system_audio 改为 false。"
            )
        if not self.capture_system and not self.capture_mic:
            raise RecorderError("配置中系统音频与麦克风均被关闭，没有可录制的内容。")

        # 整个进程只创建一个 PyAudio 实例，且所有流都在主线程打开
        self._pa = pyaudio.PyAudio()
        try:
            if self.capture_system:
                idx = _find_loopback_device(pyaudio, self._pa)
                self._sources.append(self._open_source(pyaudio, idx, "系统音频"))
            if self.capture_mic:
                idx = _find_microphone(pyaudio, self._pa)
                if idx is not None:
                    self._sources.append(self._open_source(pyaudio, idx, "麦克风"))
        except Exception:
            self._cleanup_streams()
            raise

        if not self._sources:
            self._cleanup_streams()
            raise RecorderError(
                "没有可用的音频输入源。请用 devices 命令查看设备，或检查录音配置。"
            )

        output.parent.mkdir(parents=True, exist_ok=True)
        self._output = output
        self._wave = wave.open(str(output), "wb")
        self._wave.setnchannels(1)
        self._wave.setsampwidth(2)
        self._wave.setframerate(self.sample_rate)
        # 触发一次空写入，让 WAV 文件头立刻落盘。
        # 这样即使进程被强杀，磁盘上也留有一个结构合法、可播放的部分录音，
        # 不会因为拿不到文件头而让整场会议的音频作废。
        self._wave.writeframes(b"")
        return output

    def _cleanup_streams(self) -> None:
        for src in self._sources:
            try:
                src.stream.stop_stream()
                src.stream.close()
            except Exception:
                pass
        self._sources = []
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None

    def stop(self) -> Path | None:
        self._cleanup_streams()
        if self._wave is not None:
            self._wave.close()
            self._wave = None
        return self._output

    # ---------- 采集 ----------

    def read_chunk(self) -> int:
        """采集一个时间片，混音后写入文件，返回写入的采样点数。

        两个关键设计：

        1) 必须用非阻塞读取。WASAPI loopback 在系统当前没有音频播放时
           不会产生任何数据，此时阻塞式 read() 会永久挂住，把整个录音卡死。
           实测中 loopback 在静默状态下 get_read_available() 恒为 0。
        2) 每个时间片都补齐到精确的 chunk_seconds 长度。
           某一时刻可能只有麦克风有声音而 loopback 静默，若不补静音，两路
           音频的时间轴就会错位，录音总时长也会短于实际会议时长。

        混音采用等权平均：两路同时有声音时不会削波，代价是各自衰减约 6 dB，
        该衰减对语音识别没有影响。
        """
        if self._wave is None:
            raise RecorderError("录音尚未启动。")

        np = _import_numpy()
        deadline = time.time() + self.chunk_seconds
        buffers: list[list] = [[] for _ in self._sources]

        while time.time() < deadline:
            for i, src in enumerate(self._sources):
                try:
                    avail = src.stream.get_read_available()
                except Exception:
                    avail = 0
                if avail <= 0:
                    continue
                try:
                    raw = src.stream.read(avail, exception_on_overflow=False)
                except Exception:
                    src.overflow += 1
                    continue
                if raw:
                    buffers[i].append(to_16k_mono(raw, src.rate, src.channels))
            # 恒定让步，避免有数据时把 CPU 打满
            time.sleep(0.005)

        target = int(self.chunk_seconds * self.sample_rate)

        def _fit(chunks: list) -> "np.ndarray":
            arr = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
            if arr.size >= target:
                return arr[:target]
            pad = np.zeros(target - arr.size, dtype=np.int16)
            return np.concatenate([arr, pad])

        arrays = [_fit(b) for b in buffers]
        if not arrays:
            return 0

        if len(arrays) == 1:
            mixed = arrays[0].astype(np.float32)
        else:
            mixed = np.stack([a.astype(np.float32) for a in arrays]).mean(axis=0)

        pcm = np.clip(mixed, -32768, 32767).astype(np.int16)
        self._wave.writeframes(pcm.tobytes())
        return int(pcm.size)

    # ---------- 信息 ----------

    def sources(self) -> list[str]:
        return [src.label for src in self._sources]

    def overflow_count(self) -> int:
        return sum(src.overflow for src in self._sources)


def record_session(
    cfg: Config,
    output: Path,
    duration: float | None = None,
    on_tick=None,
) -> Path:
    """按 Ctrl+C 或指定时长结束的录音会话。"""
    rec = MeetingRecorder(cfg)
    rec.start(output)
    started = time.time()
    try:
        while True:
            written = rec.read_chunk()
            elapsed = time.time() - started
            if on_tick is not None:
                on_tick(elapsed, written)
            if duration is not None and elapsed >= duration:
                break
    except KeyboardInterrupt:
        pass
    finally:
        path = rec.stop()
    return path or output
