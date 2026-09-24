"""可插拔转写后端。

统一抽象：无论用 FunASR 还是 faster-whisper，对外都返回 TranscriptResult。
切换引擎只需修改 config.yaml 中的 transcription.backend，代码零改动。

FunASR 为首选：中文专优，且一次性提供 VAD 切分、标点恢复与说话人分离，
模型体积按需下载（约 1 GB 量级），对 8 GB 内存的机器更友好。
faster-whisper 作为备选，多语种支持更好，CPU 上以 int8 量化运行。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Config, ConfigError


# ---------------------------------------------------------------- 数据结构


@dataclass
class Segment:
    """一段连续的发言。start/end 单位为秒。"""

    text: str
    start: float = 0.0
    end: float = 0.0
    speaker: str = ""

    @property
    def stamp(self) -> str:
        return format_clock(self.start)


@dataclass
class TranscriptResult:
    segments: list[Segment] = field(default_factory=list)
    backend: str = ""
    model: str = ""
    audio: str = ""
    diarized: bool = False
    elapsed: float = 0.0
    duration: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def speakers(self) -> list[str]:
        seen: list[str] = []
        for seg in self.segments:
            if seg.speaker and seg.speaker not in seen:
                seen.append(seg.speaker)
        return seen

    def to_markdown(self, audio_name: str = "") -> str:
        lines = ["# 转写文字稿", ""]
        lines.append(f"- 音频文件：{audio_name or self.audio or '(未记录)'}")
        lines.append(f"- 转写引擎：{self.backend} / {self.model}")
        if self.duration:
            lines.append(f"- 音频时长：{format_clock(self.duration)}")
        lines.append(f"- 处理耗时：{format_clock(self.elapsed)}（含模型加载的固定开销）")
        # 短音频的耗时几乎全是模型加载，算倍速没有意义，只在音频足够长时展示
        if self.duration and self.elapsed > 0 and self.duration >= 60:
            ratio = self.duration / self.elapsed
            lines.append(f"- 处理速率：约 {ratio:.1f} 倍速（相对音频时长）")
        if self.diarized and self.speakers:
            lines.append(f"- 说话人分离：已启用，识别到 {len(self.speakers)} 人")
        else:
            lines.append("- 说话人分离：未启用")

        gloss = (self.meta or {}).get("glossary")
        if gloss and gloss.get("enabled"):
            from .glossary import describe_stats

            lines.append(f"- 术语规范化：{describe_stats(gloss)}")
            for label, count in (gloss.get("hits") or {}).items():
                lines.append(f"    - {label}（{count} 处）")

        merge = (self.meta or {}).get("merge")
        if merge and merge.get("after", 0) < merge.get("before", 0):
            lines.append(
                f"- 段落合并：{merge['before']} 段 → {merge['after']} 段"
                f"（同说话人相邻短句已归并为完整段落）"
            )
        lines.append("")
        lines.append("---")
        lines.append("")
        for seg in self.segments:
            head = f"**[{seg.stamp}]**"
            if seg.speaker:
                head += f" **{seg.speaker}**"
            lines.append(head)
            lines.append("")
            lines.append(seg.text.strip())
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def to_prompt_text(self) -> str:
        """供大模型消费的紧凑文本：保留说话人与时间，去掉排版噪声。"""
        blocks: list[str] = []
        for seg in self.segments:
            prefix = f"[{seg.stamp}]"
            if seg.speaker:
                prefix += f"{seg.speaker}"
            blocks.append(f"{prefix} {seg.text.strip()}")
        return "\n".join(blocks)


def format_clock(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


# ---------------------------------------------------------------- 运行环境


def prepare_runtime_env(cfg: Config) -> dict[str, str]:
    """把模型与缓存目录全部指向 D 盘。

    必须在导入 torch / funasr / modelscope 之前调用，否则这些库已经
    按默认路径（C 盘用户目录）初始化了缓存位置。本机 C 盘仅剩 8 GB，
    这是必须遵守的约束。
    """
    model_dir = cfg.path("model_dir")
    cache_dir = cfg.path("cache_dir")
    for p in (model_dir, cache_dir):
        p.mkdir(parents=True, exist_ok=True)

    mapping = {
        "MODELSCOPE_CACHE": model_dir,
        "HF_HOME": cache_dir,
        "HF_HUB_CACHE": cache_dir,
        "HUGGINGFACE_HUB_CACHE": cache_dir,
        "TORCH_HOME": cache_dir / "torch",
        "XDG_CACHE_HOME": cache_dir,
        "PIP_CACHE_DIR": cache_dir / "pip",
        "TMPDIR": cache_dir / "tmp",
    }
    applied: dict[str, str] = {}
    for key, value in mapping.items():
        text = str(value)
        os.environ[key] = text
        applied[key] = text
        if key == "TMPDIR":
            Path(text).mkdir(parents=True, exist_ok=True)

    # HuggingFace 镜像：国内直连 huggingface.co 不通，实测 hf-mirror.com 可用。
    # 必须在任何 huggingface_hub / faster_whisper 导入之前设置。
    hf_endpoint = (cfg.get("transcription", "faster_whisper", "hf_endpoint") or "").strip()
    if hf_endpoint:
        os.environ["HF_ENDPOINT"] = hf_endpoint
        applied["HF_ENDPOINT"] = hf_endpoint

    # tempfile 会在首次调用时缓存解析结果，之后改环境变量就不再生效。
    # FunASR 依赖的 jieba 会往临时目录写约 9 MB 的词表缓存，若不强制覆盖，
    # 它会落到 C 盘用户 Temp 目录，与"所有大文件都留在 D 盘"的原则相悖。
    import tempfile

    temp_root = cache_dir / "tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(temp_root)
    applied["tempfile.tempdir"] = str(temp_root)

    # FunASR / modelscope 关闭联网检查更新，避免每次启动都发起网络请求
    os.environ["MODELSCOPE_OFFLINE"] = os.environ.get("MODELSCOPE_OFFLINE", "0")
    return applied


# ---------------------------------------------------------------- 音频预处理


def _which(cmd: str) -> str | None:
    return shutil.which(cmd)


def ensure_wav(audio_path: Path, work_dir: Path, sample_rate: int = 16000) -> Path:
    """确保得到 16 kHz 单声道 WAV。

    录音模块直接产出符合要求的 WAV，因此这里是针对手机录音、会议软件
    导出等外部来源的兼容处理。依赖 ffmpeg，缺失时原样返回并交由后端处理。
    """
    if audio_path.suffix.lower() == ".wav":
        return audio_path
    ffmpeg = _which("ffmpeg")
    if not ffmpeg:
        return audio_path
    work_dir.mkdir(parents=True, exist_ok=True)
    target = work_dir / (audio_path.stem + ".16k.wav")
    if target.exists() and target.stat().st_mtime >= audio_path.stat().st_mtime:
        return target
    cmd = [
        ffmpeg, "-y", "-i", str(audio_path),
        "-ac", "1", "-ar", str(sample_rate),
        "-vn", "-loglevel", "error",
        str(target),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return target


def probe_duration(audio_path: Path) -> float | None:
    """尽力探测音频时长，失败返回 None（不影响主流程）。

    依次尝试 soundfile、wave、ffprobe。前两者只认 wav 一类无损格式，
    手机录音常见的 m4a/mp3 需要靠 ffprobe —— 界面上要显示时长，
    所以这一路不能省。
    """
    try:
        import soundfile as sf  # type: ignore

        info = sf.info(str(audio_path))
        return float(info.frames) / float(info.samplerate)
    except Exception:
        pass

    try:
        import wave

        with wave.open(str(audio_path), "rb") as fh:
            return fh.getnframes() / float(fh.getframerate())
    except Exception:
        pass

    return _probe_duration_ffprobe(audio_path)


def _probe_duration_ffprobe(audio_path: Path) -> float | None:
    """用 ffprobe 读取时长。ffprobe 通常与 ffmpeg 一同安装。"""
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    try:
        result = subprocess.run(
            [
                exe, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        text = (result.stdout or "").strip()
        return float(text) if text else None
    except Exception:
        return None


# ---------------------------------------------------------------- 抽象接口


class BaseTranscriber(ABC):
    name = "base"

    def __init__(self, cfg: Config, conf: dict):
        self.cfg = cfg
        self.conf = conf or {}

    @classmethod
    @abstractmethod
    def available(cls) -> tuple[bool, str]:
        """返回 (依赖是否就绪, 说明)。用于自检命令，不加载模型。"""

    @abstractmethod
    def transcribe(self, audio_path: Path) -> TranscriptResult:
        ...

    def release(self) -> None:
        """释放已加载的模型，归还内存。批量处理结束后可主动调用。"""
        self._model = None
        try:
            import gc

            gc.collect()
        except Exception:
            pass

    def describe(self) -> str:
        return f"{self.name}（{self.conf.get('model', '?')}）"


# ---------------------------------------------------------------- FunASR


class FunASRTranscriber(BaseTranscriber):
    """阿里达摩院 FunASR。中文首选，自带 VAD、标点与说话人分离。"""

    name = "funasr"

    def __init__(self, cfg: Config, conf: dict):
        super().__init__(cfg, conf)
        # 模型加载是本机最贵的一步（约 75 秒），实例内缓存以便批量处理时
        # 只付一次代价。多个音频请复用同一个实例。
        self._model = None
        self._has_spk = False

    @classmethod
    def available(cls) -> tuple[bool, str]:
        try:
            import funasr  # type: ignore  # noqa: F401
        except ImportError:
            return False, "未安装 funasr，执行：pip install funasr modelscope torch torchaudio"
        try:
            import torch  # type: ignore  # noqa: F401
        except ImportError:
            return False, "未安装 torch，执行：pip install torch torchaudio"
        return True, "依赖就绪"

    def _build_model(self):
        if self._model is not None:
            return self._model, self._has_spk

        from funasr import AutoModel  # type: ignore

        spk = self.conf.get("spk_model") or None
        kwargs: dict[str, Any] = {
            "model": self.conf.get("model", "paraformer-zh"),
            "device": self.conf.get("device", "cpu"),
            "ncpu": int(self.conf.get("ncpu", 4)),
            "disable_update": bool(self.conf.get("disable_update", True)),
            "disable_pbar": True,
        }
        if self.conf.get("vad_model"):
            kwargs["vad_model"] = self.conf["vad_model"]
        if self.conf.get("punc_model"):
            kwargs["punc_model"] = self.conf["punc_model"]
        if spk:
            kwargs["spk_model"] = spk
        self._model = AutoModel(**kwargs)
        self._has_spk = bool(spk)
        return self._model, self._has_spk

    def transcribe(self, audio_path: Path) -> TranscriptResult:
        model, has_spk = self._build_model()

        hotword = normalize_hotword(self.conf.get("hotword"))
        call: dict[str, Any] = {
            "input": str(audio_path),
            "batch_size_s": int(self.conf.get("batch_size_s", 300)),
        }
        if hotword:
            call["hotword"] = hotword

        raw = model.generate(**call)
        segments = _parse_funasr_output(raw)
        return TranscriptResult(
            segments=segments,
            backend=self.name,
            model=str(self.conf.get("model", "paraformer-zh")),
            audio=str(audio_path),
            diarized=has_spk and any(s.speaker for s in segments),
        )


def merge_segments(
    segments: list[Segment],
    max_gap: float = 4.0,
    max_duration: float = 90.0,
    max_chars: int = 400,
) -> list[Segment]:
    """把同一说话人相邻且间隔很短的段落合并成完整段落。

    为什么需要：转写引擎为了对齐时间戳，会把一句完整的话切成好几个小段。
    实测一场 47 分钟的真实会议，1218 段、平均每段仅 10.9 字、25% 的段不足
    5 字。这种碎片化的文字稿有两个害处：
      1. 人读起来断断续续，无法快速把握语义；
      2. 大模型也更容易丢失上下文，导致纪要的条目割裂。
    合并后既便于阅读，也明显提升纪要质量。

    max_gap：相邻两段的开始时间间隔超过此值就不合并（说明中间有停顿或换人）。
    max_duration / max_chars：防止合并出超长段落，超过即另起一段。
    """
    if not segments:
        return []

    def speaker_of(seg: Segment) -> str:
        return seg.speaker or ""

    merged: list[Segment] = []
    current = Segment(
        text=segments[0].text,
        start=segments[0].start,
        end=segments[0].end,
        speaker=segments[0].speaker,
    )

    for nxt in segments[1:]:
        same_speaker = speaker_of(nxt) == speaker_of(current)

        # 优先用真实的结束时间计算间隔；没有结束时间时退化为比较开始时间
        if current.end and nxt.start:
            gap = nxt.start - current.end
        else:
            gap = nxt.start - current.start

        would_duration = (nxt.end or nxt.start) - current.start
        would_chars = len(current.text) + len(nxt.text)

        if (
            same_speaker
            and gap <= max_gap
            and would_duration <= max_duration
            and would_chars <= max_chars
        ):
            # 中文直接连接，不加空格
            current.text = current.text + nxt.text
            current.end = nxt.end or current.end
        else:
            merged.append(current)
            current = Segment(
                text=nxt.text, start=nxt.start, end=nxt.end, speaker=nxt.speaker
            )

    merged.append(current)
    return [seg for seg in merged if seg.text.strip()]


def normalize_hotword(raw: Any) -> str:
    """把热词整理成 FunASR 需要的空格分隔格式。

    配置里用逗号、顿号书写更符合中文习惯，这里统一转换，并去重、去空。
    """
    if not raw:
        return ""
    if isinstance(raw, (list, tuple)):
        parts = [str(x) for x in raw]
    else:
        parts = re.split(r"[,，、;；\s]+", str(raw))
    seen: list[str] = []
    for item in parts:
        word = item.strip()
        if word and word not in seen:
            seen.append(word)
    return " ".join(seen)


def _parse_funasr_output(raw: Any) -> list[Segment]:
    """解析 FunASR 输出。

    带 spk_model 时结果落在 sentence_info 中（含说话人编号与毫秒级时间戳）；
    不带时只有整段 text。两种形态都要能处理，因此这里做兼容解析。
    """
    segments: list[Segment] = []
    if not isinstance(raw, list):
        raw = [raw]

    for item in raw:
        if not isinstance(item, dict):
            continue

        sentence_info = item.get("sentence_info")
        if isinstance(sentence_info, list) and sentence_info:
            for sent in sentence_info:
                if not isinstance(sent, dict):
                    continue
                text = (sent.get("text") or "").strip()
                if not text:
                    continue
                spk = sent.get("spk", sent.get("speaker", ""))
                if isinstance(spk, int):
                    speaker = f"说话人{spk + 1}"
                elif isinstance(spk, str) and spk.strip():
                    speaker = spk.strip()
                else:
                    speaker = ""
                segments.append(
                    Segment(
                        text=text,
                        start=float(sent.get("start", 0) or 0) / 1000.0,
                        end=float(sent.get("end", 0) or 0) / 1000.0,
                        speaker=speaker,
                    )
                )
            continue

        text = (item.get("text") or "").strip()
        if text:
            segments.append(Segment(text=text))

    return segments


# ---------------------------------------------------------------- faster-whisper


class FasterWhisperTranscriber(BaseTranscriber):
    """CTranslate2 加速版 Whisper。多语种，CPU 上以 int8 量化运行。"""

    name = "faster_whisper"

    # faster-whisper 加载模型所需的最小文件集
    _REQUIRED_FILES = ("config.json", "model.bin", "tokenizer.json")
    _OPTIONAL_FILES = ("vocabulary.txt", "vocabulary.json", "preprocessor_config.json")

    def __init__(self, cfg: Config, conf: dict):
        super().__init__(cfg, conf)
        self._model = None
        self._device = ""

    @classmethod
    def available(cls) -> tuple[bool, str]:
        try:
            import faster_whisper  # type: ignore  # noqa: F401
        except ImportError:
            return False, "未安装 faster-whisper，执行：pip install faster-whisper"
        return True, "依赖就绪"

    def _endpoint(self) -> str:
        return (self.conf.get("hf_endpoint") or "https://huggingface.co").rstrip("/")

    def _model_dir(self) -> Path:
        size = str(self.conf.get("model", "small"))
        return self.cfg.path("model_dir") / "faster-whisper" / size

    def _ensure_model_files(self, target: Path, verbose: bool = True) -> Path:
        """把模型文件下载到指定的平坦目录。

        刻意不走 huggingface_hub 的缓存机制：在 Windows 上若未开启开发者模式，
        hub 无法创建符号链接，会在快照目录留下 0 字节的占位文件，模型加载时
        报 "File model.bin is incomplete"。实测即为此故障，且此时数据其实已经
        完整下载进了 blobs，只是链接没建成。

        这里直接从镜像逐文件下载到平坦目录，既绕开符号链接问题，也能确保
        模型落在 D 盘而非 C 盘用户目录。
        """
        import requests

        target.mkdir(parents=True, exist_ok=True)
        size = str(self.conf.get("model", "small"))
        repo = f"Systran/faster-whisper-{size}"
        endpoint = self._endpoint()

        def _ok(name: str) -> bool:
            f = target / name
            return f.exists() and f.stat().st_size > 0

        if all(_ok(n) for n in self._REQUIRED_FILES):
            return target

        if verbose:
            print(f"       正在从 {endpoint} 下载模型 {repo}（首次使用，约 500 MB）……")

        for name in self._REQUIRED_FILES + self._OPTIONAL_FILES:
            if _ok(name):
                continue
            url = f"{endpoint}/{repo}/resolve/main/{name}"
            dest = target / name
            tmp = dest.with_suffix(dest.suffix + ".part")
            try:
                with requests.get(url, timeout=120, stream=True, allow_redirects=True) as resp:
                    if resp.status_code != 200:
                        if name in self._OPTIONAL_FILES:
                            continue
                        raise RuntimeError(
                            f"下载 {name} 失败（HTTP {resp.status_code}）。\n"
                            f"下载地址：{url}\n"
                            f"若网络不通，请在 config.yaml 的 "
                            f"transcription.faster_whisper.hf_endpoint 中更换镜像。"
                        )
                    with tmp.open("wb") as fh:
                        for block in resp.iter_content(chunk_size=1 << 20):
                            if block:
                                fh.write(block)
                tmp.replace(dest)
                if verbose:
                    print(f"         {name}  {dest.stat().st_size / 1024**2:.1f} MB")
            except requests.exceptions.RequestException as exc:
                tmp.unlink(missing_ok=True)
                if name in self._OPTIONAL_FILES:
                    continue
                raise RuntimeError(
                    f"下载模型文件 {name} 失败：{exc}\n"
                    f"当前镜像：{endpoint}\n"
                    f"可在 config.yaml 的 transcription.faster_whisper.hf_endpoint "
                    f"中更换为其他可用镜像。"
                ) from exc

        missing = [n for n in self._REQUIRED_FILES if not _ok(n)]
        if missing:
            raise RuntimeError(
                f"模型文件不完整，缺少：{'、'.join(missing)}\n目录：{target}"
            )
        return target

    def _build_model(self):
        if self._model is not None:
            return self._model

        # 仍需设置镜像，供 faster_whisper 内部可能发起的校验请求使用。
        # 必须在导入 huggingface_hub 之前设置，它是在 import 时读取该变量的。
        endpoint = (self.conf.get("hf_endpoint") or "").strip()
        if endpoint:
            os.environ["HF_ENDPOINT"] = endpoint

        from faster_whisper import WhisperModel  # type: ignore

        size = str(self.conf.get("model", "small"))
        device = self.conf.get("device", "auto")
        if device == "auto":
            device = "cpu"
            try:
                import torch  # type: ignore

                if torch.cuda.is_available():
                    device = "cuda"
            except Exception:
                pass

        model_dir = self._ensure_model_files(self._model_dir())
        compute_type = self.conf.get("compute_type", "int8")

        # 传目录路径而非模型名，避免 faster-whisper 再去访问 HuggingFace
        self._model = WhisperModel(
            str(model_dir),
            device=device,
            compute_type=compute_type,
        )
        self._device = f"{device}/{compute_type}"
        return self._model

    def transcribe(self, audio_path: Path) -> TranscriptResult:
        model = self._build_model()

        language = (self.conf.get("language") or "").strip() or None
        prompt = (self.conf.get("initial_prompt") or "").strip() or None

        seg_iter, info = model.transcribe(
            str(audio_path),
            language=language,
            beam_size=int(self.conf.get("beam_size", 5)),
            vad_filter=bool(self.conf.get("vad_filter", True)),
            initial_prompt=prompt,
        )

        segments = [
            Segment(
                text=s.text.strip(),
                start=float(s.start or 0),
                end=float(s.end or 0),
            )
            for s in seg_iter
            if (s.text or "").strip()
        ]

        return TranscriptResult(
            segments=segments,
            backend=self.name,
            model=f"{self.conf.get('model', 'small')} ({self._device})",
            audio=str(audio_path),
            diarized=False,
            meta={"language": getattr(info, "language", None)},
        )


# ---------------------------------------------------------------- 工厂


_REGISTRY: dict[str, type[BaseTranscriber]] = {
    FunASRTranscriber.name: FunASRTranscriber,
    FasterWhisperTranscriber.name: FasterWhisperTranscriber,
}


def available_backends() -> dict[str, tuple[bool, str]]:
    return {name: cls.available() for name, cls in _REGISTRY.items()}


def get_transcriber(cfg: Config, backend: str | None = None) -> BaseTranscriber:
    name = (backend or cfg.backend_name).strip().lower()
    if name not in _REGISTRY:
        raise ConfigError(
            f"未知转写引擎 '{name}'。可选：{'、'.join(_REGISTRY)}"
        )
    conf = cfg.backend_conf(name)
    if not conf:
        raise ConfigError(f"配置中缺少 transcription.{name} 段")
    return _REGISTRY[name](cfg, conf)


def run_transcription(
    cfg: Config,
    audio_path: Path,
    backend: str | None = None,
    transcriber: BaseTranscriber | None = None,
) -> TranscriptResult:
    """执行转写，统一处理音频格式转换与耗时统计。

    传入 transcriber 可复用已加载的模型。批量处理多个音频时务必复用，
    否则每个文件都要重新付出约 75 秒的模型加载代价（本机实测）。
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"音频文件不存在：{audio_path}")

    prepare_runtime_env(cfg)
    if transcriber is None:
        transcriber = get_transcriber(cfg, backend)

    work_dir = cfg.path("cache_dir") / "audio"
    usable = ensure_wav(audio_path, work_dir, int(cfg.get("recording", "sample_rate", default=16000)))

    started = time.time()
    result = transcriber.transcribe(usable)
    result.elapsed = time.time() - started
    result.audio = str(audio_path)
    if result.duration is None:
        result.duration = probe_duration(usable)

    if not result.segments:
        raise RuntimeError(
            "转写结果为空。可能原因：音频无人声、采样率异常，或模型未正确加载。"
        )

    # 合并同说话人的碎片段落，让文字稿可读、也让模型更好地理解上下文
    merge_conf = cfg.get("transcription", "merge") or {}
    if merge_conf.get("enabled", True) and len(result.segments) > 1:
        before = len(result.segments)
        result.segments = merge_segments(
            result.segments,
            max_gap=float(merge_conf.get("max_gap_s", 4.0)),
            max_duration=float(merge_conf.get("max_duration_s", 90)),
            max_chars=int(merge_conf.get("max_chars", 400)),
        )
        result.meta["merge"] = {"before": before, "after": len(result.segments)}

    # 术语规范化：修正同音误识别、统一写法。放在最后一步，
    # 这样对已有文字稿重跑也能拿到一致结果。
    from .glossary import apply_to_result

    result, _glossary_stats = apply_to_result(result, cfg)
    return result
