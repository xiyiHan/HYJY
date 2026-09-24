r"""录音链路诊断 —— 在正式使用前验证麦克风与系统音频是否都能采到声音。

运行：
    .venv\Scripts\python.exe tests\test_recording.py
    .venv\Scripts\python.exe tests\test_recording.py --seconds 10
    .venv\Scripts\python.exe tests\test_recording.py --play workspace\audio\某录音.wav

它会做三件事：
  1. 逐个设备非阻塞采集若干秒，报告各自实际采到的数据量与音量；
  2. 走一遍完整的 MeetingRecorder 流程，确认混音与写盘正常；
  3. 给出可操作的结论。

为什么需要这个脚本：WASAPI loopback 只在系统正在播放声音时才产出数据。
如果诊断时没有播放任何音频，"系统音频" 这一路就会显示为静音 ——
这属于正常现象，用 --play 指定一个音频文件即可验证。
"""

from __future__ import annotations

import argparse
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

from mmtools.config import Config  # noqa: E402
from mmtools.recorder import (  # noqa: E402
    MeetingRecorder,
    RecorderError,
    _find_loopback_device,
    _find_microphone,
    to_16k_mono,
)


def _rms_db(arr) -> float:
    """计算音频电平（dBFS）。全静音返回负无穷。"""
    import math

    import numpy as np

    if arr is None or arr.size == 0:
        return float("-inf")
    rms = float(np.sqrt(np.mean(arr.astype(np.float64) ** 2)))
    if rms <= 0:
        return float("-inf")
    return 20.0 * math.log10(rms / 32768.0)


def _play_async(path: Path) -> bool:
    try:
        import winsound

        winsound.PlaySound(
            str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP
        )
        return True
    except Exception as exc:
        print(f"  无法播放测试音频：{exc}")
        return False


def _stop_play() -> None:
    try:
        import winsound

        winsound.PlaySound(None, winsound.SND_PURGE)
    except Exception:
        pass


def probe_device(pyaudio, p, index: int, label: str, seconds: float) -> dict:
    """非阻塞采集单个设备，测量实际拿到多少数据。"""
    import numpy as np

    info = p.get_device_info_by_index(index)
    rate = int(info["defaultSampleRate"])
    channels = max(1, min(2, int(info.get("maxInputChannels") or 1)))
    result = {
        "label": label,
        "name": info.get("name"),
        "rate": rate,
        "channels": channels,
        "samples": 0,
        "chunks": 0,
        "rms_db": float("-inf"),
    }

    stream = p.open(
        format=pyaudio.paInt16,
        channels=channels,
        rate=rate,
        frames_per_buffer=1024,
        input=True,
        input_device_index=index,
        start=False,
    )
    stream.start_stream()
    collected: list = []
    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            avail = stream.get_read_available()
            if avail > 0:
                raw = stream.read(avail, exception_on_overflow=False)
                if raw:
                    arr = to_16k_mono(raw, rate, channels)
                    collected.append(arr)
                    result["chunks"] += 1
            time.sleep(0.005)
    finally:
        stream.stop_stream()
        stream.close()

    if collected:
        merged = np.concatenate(collected)
        result["samples"] = int(merged.size)
        result["rms_db"] = _rms_db(merged)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="录音链路诊断")
    parser.add_argument("--seconds", type=float, default=6.0, help="每个设备的采集时长")
    parser.add_argument("--play", help="采集期间循环播放的音频文件，用于验证系统音频采集")
    args = parser.parse_args()

    try:
        import pyaudiowpatch as pyaudio
    except ImportError:
        print("未安装 pyaudiowpatch，请执行：pip install pyaudiowpatch numpy")
        return 2

    import numpy as np  # noqa: F401

    cfg = Config.load()
    print("=" * 62)
    print("录音链路诊断")
    print("=" * 62)
    print("")

    playing = False
    if args.play:
        audio = Path(args.play)
        if not audio.is_absolute():
            audio = ROOT / audio
        if not audio.exists():
            print(f"文件不存在：{audio}")
            return 2
        print(f"采集期间循环播放：{audio.name}")
        playing = _play_async(audio)

    p = pyaudio.PyAudio()
    reports = []
    try:
        try:
            idx = _find_loopback_device(pyaudio, p)
            print(f"采集系统音频 {args.seconds:.0f} 秒……")
            reports.append(probe_device(pyaudio, p, idx, "系统音频", args.seconds))
        except RecorderError as exc:
            print(f"  系统音频不可用：{exc}")

        try:
            idx = _find_microphone(pyaudio, p)
            if idx is None:
                print("  未找到默认麦克风")
            else:
                print(f"采集麦克风 {args.seconds:.0f} 秒……（可对着麦克风说话）")
                reports.append(probe_device(pyaudio, p, idx, "麦克风", args.seconds))
        except Exception as exc:
            print(f"  麦克风不可用：{exc}")
    finally:
        p.terminate()
        if playing:
            _stop_play()

    print("")
    print("-" * 62)
    print("各设备采集结果")
    print("-" * 62)
    for r in reports:
        expected = int(args.seconds * 16000)
        ratio = (r["samples"] / expected * 100) if expected else 0
        level = r["rms_db"]
        level_text = "静音" if level == float("-inf") else f"{level:.1f} dBFS"
        print(f"  {r['label']}：{r['name']}")
        print(f"     采样率 {r['rate']} Hz / {r['channels']} 声道")
        print(f"     采集到 {r['samples']} 采样点（预期约 {expected}，达成率 {ratio:.0f}%）")
        print(f"     音量 {level_text}，读取 {r['chunks']} 次")
        print("")

    print("-" * 62)
    print("结论")
    print("-" * 62)
    ok = True
    for r in reports:
        if r["samples"] == 0:
            print(f"  [{r['label']}] 完全没有采到数据，需要排查设备或驱动")
            ok = False
        elif r["rms_db"] == float("-inf"):
            if r["label"] == "系统音频" and not playing:
                print(
                    f"  [系统音频] 采到数据流但内容为静音 —— 这是因为诊断时系统没有"
                    f"播放任何声音，属正常现象。\n"
                    f"             请用 --play 指定一个音频文件复测。"
                )
            else:
                print(f"  [{r['label']}] 采到数据但内容为静音，请检查输入源是否有声音")
                ok = False
        else:
            print(f"  [{r['label']}] 正常，有效音量 {r['rms_db']:.1f} dBFS")

    # 再走一遍完整录音流程
    print("")
    print(f"完整流程测试（{min(3.0, args.seconds):.0f} 秒）……")
    out = cfg.path("audio") / "_录音诊断.wav"
    cfg.ensure_dirs()
    rec = MeetingRecorder(cfg)
    rec.chunk_seconds = max(1, int(min(3.0, args.seconds)))
    try:
        rec.start(out)
        print(f"  已打开音频源：{rec.sources()}")
        n = rec.read_chunk()
        rec.stop()
        with wave.open(str(out), "rb") as fh:
            seconds = fh.getnframes() / fh.getframerate()
            print(f"  写入 {n} 采样点，文件时长 {seconds:.2f} 秒，"
                  f"格式 {fh.getframerate()} Hz / {fh.getnchannels()} 声道")
        if abs(seconds - rec.chunk_seconds) > 0.1:
            print(f"  警告：文件时长与预期 {rec.chunk_seconds} 秒不符")
            ok = False
        else:
            print("  时间轴长度正确")
    except RecorderError as exc:
        print(f"  完整流程失败：{exc}")
        ok = False

    print("")
    print("诊断完成：" + ("录音链路可用。" if ok else "存在问题，请按上方提示排查。"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
