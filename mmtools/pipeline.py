"""端到端流水线：录音 → 本地转写 → 文字稿 → 云端纪要 → 归档。

设计上刻意把每一步做成独立函数，且各步产物都落盘：
  1) 转写失败不会丢掉录音；
  2) 纪要失败时可以直接对已生成的文字稿重跑，不必重新转写
     —— 这一点对本机尤其重要，转写一次要花几十分钟。
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import prompts
from .config import Config
from .progress import CancelledError, Progress
from .summarizer import CloudLLM
from .transcribers import (
    Segment,
    TranscriptResult,
    get_transcriber,
    prepare_runtime_env,
    run_transcription,
)


@dataclass
class PipelineResult:
    audio: Path
    transcript_path: Path | None = None
    minutes_path: Path | None = None
    docx_path: Path | None = None
    transcript: TranscriptResult | None = None
    stats: dict | None = None


def slugify(text: str) -> str:
    """把标题整理成安全的文件名片段。"""
    bad = '<>:"/\\|?*\n\r\t'
    cleaned = "".join("_" if ch in bad else ch for ch in text).strip(" .")
    return cleaned[:60] or "meeting"


# 兼容旧调用名
_slug = slugify


def default_stem(audio: Path | None = None, title: str = "") -> str:
    stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    if title:
        return f"{stamp}_{slugify(title)}"
    if audio:
        return slugify(audio.stem)
    return stamp


# ---------------------------------------------------------------- 转写步骤


def build_form_preview(
    cfg: Config,
    transcript_text: str,
    stem: str,
    known: dict | None = None,
    provider: str | None = None,
) -> Path:
    """预览表单模式将发送到云端的内容。"""
    llm = CloudLLM(cfg, provider)
    system_prompt = prompts.get_system_prompt("supervision_form")
    user_prompt = prompts.build_form_user_prompt(transcript_text, known)

    out_dir = cfg.path("minutes")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{stem}.prompt.md"

    body = [
        "<!--",
        "  表单模式：以下是「将要发送到云端」的完整内容预览，本文件不会出网。",
        f"  目标平台：{llm.label}",
        f"  接口地址：{llm.endpoint}",
        f"  使用模型：{llm.model}",
        f"  合规说明：{llm.compliance}",
        "  确认无误后，去掉 --dry-run 重新运行即可实际生成。",
        "-->",
        "",
        f"## 一、系统提示词（{len(system_prompt)} 字符）",
        "",
        "```text",
        system_prompt.strip(),
        "```",
        "",
        f"## 二、用户提示词（{len(user_prompt)} 字符）",
        "",
        "```text",
        user_prompt.strip(),
        "```",
        "",
    ]
    target.write_text("\n".join(body), encoding="utf-8")
    return target


def step_summarize_form(
    cfg: Config,
    result: TranscriptResult,
    stem: str,
    provider: str | None = None,
    verbose: bool = True,
    dry_run: bool = False,
    template_path: Path | None = None,
    known: dict | None = None,
    progress: Progress | None = None,
) -> tuple[Path, dict]:
    """表单模式：生成结构化数据并填充单位正式模板。

    输出三份文件：
      - <stem>.minutes.json   结构化数据，便于追溯模型究竟填了什么
      - <stem>.minutes.md     可读的文字版，便于人工复核
      - <stem>.minutes.docx   填充后的正式模板文件
    """
    progress = progress or Progress(echo=verbose)
    transcript_text = result.to_prompt_text()

    if dry_run:
        target = build_form_preview(cfg, transcript_text, stem, known, provider)
        llm = CloudLLM(cfg, provider)
        progress.log(f"[纪要] 已跳过实际调用（--dry-run），目标平台 {llm.describe()}")
        progress.log(f"       出网内容预览已保存：{target.name}")
        return target, {"dry_run": True}

    llm = CloudLLM(cfg, provider)
    progress.log(f"[纪要] 生成结构化数据中 —— 平台 {llm.describe()}")
    progress.log(f"       数据合规提示：{llm.compliance}")
    progress.report("summarize", "生成纪要中", provider=llm.label, model=llm.model)

    data, stats = llm.summarize_form(transcript_text, known)

    out_dir = cfg.path("minutes")
    out_dir.mkdir(parents=True, exist_ok=True)

    # 结构化数据落盘，便于追溯与二次调整
    json_path = out_dir / f"{stem}.minutes.json"
    json_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 同时产出可读版本，供人工复核后再提交
    md_path = out_dir / f"{stem}.minutes.md"
    md_path.write_text(form_data_to_markdown(data, result, llm, stats), encoding="utf-8")

    progress.log(
        f"       完成：输入 {stats.get('prompt_tokens', 0)} tokens，"
        f"输出 {stats.get('completion_tokens', 0)} tokens"
    )
    progress.log(f"       议定事项 {len(data.get('items') or [])} 条，"
                 f"参会单位 {len(data.get('attendees') or [])} 个")

    # 填充模板
    path = template_path or (cfg.get("template", "path") or "")
    if not path:
        progress.log("       未提供模板路径，已跳过 Word 模板填充")
        progress.log(f"       数据已保存：{json_path.name}")
        progress.report("summarize_done", "纪要已生成", minutes=str(md_path))
        return md_path, stats

    from .docx_template import fill_official_minutes

    progress.report("fill", "填充单位模板中")
    docx_path = out_dir / f"{stem}.minutes.docx"
    fill_official_minutes(cfg, Path(path), docx_path, data)
    progress.log(f"       正式模板已填充：{docx_path.name}")
    progress.report(
        "summarize_done",
        "纪要已生成",
        minutes=str(docx_path),
        json=str(json_path),
        items=len(data.get("items") or []),
    )
    return docx_path, stats


def form_data_to_markdown(data: dict, result: TranscriptResult, llm, stats: dict) -> str:
    """把结构化数据渲染成可读 Markdown，便于人工复核。"""
    lines = [
        "<!--",
        f"  生成时间：{_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"  转写引擎：{result.backend} / {result.model}",
        f"  纪要平台：{llm.label} / {stats.get('model', '')}",
        f"  用量统计：输入 {stats.get('prompt_tokens', 0)} tokens，"
        f"输出 {stats.get('completion_tokens', 0)} tokens",
        "  说明：这是填充单位模板前的人工复核稿，正式文件为同名 .docx。",
        "  纪要为机器生成，提交前须人工复核。",
        "-->",
        "",
        f"# {data.get('project', '')} 会议纪要",
        "",
        f"**（{data.get('meeting_no', '')}）**",
        "",
        "## 一、成文信息",
        "",
        "| 项目 | 内容 |",
        "| --- | --- |",
        f"| 会议类型 | {data.get('meeting_type', '')} |",
        f"| 会议主题 | {data.get('topic', '')} |",
        f"| 会议地点 | {data.get('location', '')} |",
        f"| 会议时间 | {data.get('time', '')} |",
        f"| 会议主持 | {data.get('host', '')} |",
        f"| 会议记录 | {data.get('recorder', '')} |",
        f"| 发布日期 | {data.get('publish_date', '')} |",
        "",
        "## 二、签收信息",
        "",
        "| 单位 | 代表人 | 日期 |",
        "| --- | --- | --- |",
    ]
    sign = data.get("signatories") or {}
    for label, name in (
        ("建设单位", data.get("owner_unit")),
        ("承建单位", data.get("builder_unit")),
        ("监理单位", data.get("supervisor_unit")),
    ):
        detail = sign.get(label) or {}
        lines.append(
            f"| {name or '（未提及）'} | {detail.get('rep') or ''} | {detail.get('date') or ''} |"
        )

    lines += [
        "",
        "## 三、会议纪要正文",
        "",
        data.get("intro", ""),
        "",
        "会议纪要如下：",
        "",
    ]
    for i, item in enumerate(data.get("items") or [], start=1):
        lines.append(f"{i}、{item}")
        lines.append("")

    lines += ["出席：", ""]
    for item in data.get("attendees") or []:
        names = "  ".join(item.get("people") or [])
        lines.append(f"{item.get('unit', '')}：{names}" if names else f"{item.get('unit', '')}：")

    lines.append("")
    return "\n".join(lines)


def _backend_of_transcript(path: Path) -> str:
    """从已有文字稿的元信息里读出它是由哪个引擎生成的。"""
    try:
        for line in path.read_text(encoding="utf-8").splitlines()[:12]:
            if "转写引擎：" in line:
                return line.split("转写引擎：", 1)[1].split("/")[0].strip()
    except Exception:
        pass
    return ""


def step_transcribe(
    cfg: Config,
    audio: Path,
    stem: str | None = None,
    backend: str | None = None,
    verbose: bool = True,
    transcriber=None,
    progress: Progress | None = None,
) -> tuple[TranscriptResult, Path]:
    progress = progress or Progress(echo=verbose)
    audio = Path(audio)
    stem = stem or default_stem(audio)

    from .transcribers import format_clock, get_transcriber

    tr = transcriber or get_transcriber(cfg, backend)
    progress.log(f"[转写] {audio.name} —— 引擎 {tr.describe()}")
    if tr.name == "funasr" and tr.conf.get("spk_model"):
        progress.log(f"       说话人分离已启用（{tr.conf['spk_model']}）")

    progress.report("transcribe", "开始转写", engine=tr.describe(), file=audio.name)

    result = run_transcription(cfg, audio, backend, transcriber=transcriber)

    out_dir = cfg.path("transcript")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{stem}.transcript.md"

    # 换引擎重跑时不要静默覆盖，把旧文字稿按引擎名归档，便于对比与回溯
    if target.exists():
        old_backend = _backend_of_transcript(target)
        if old_backend and old_backend != result.backend:
            backup = out_dir / f"{stem}.{old_backend}.transcript.md"
            target.replace(backup)
            progress.log(f"       旧的 {old_backend} 文字稿已归档为：{backup.name}")
        else:
            progress.log("       覆盖已有的同引擎文字稿")

    target.write_text(result.to_markdown(audio.name), encoding="utf-8")

    progress.log(f"       完成：{len(result.segments)} 段，耗时 {result.elapsed:.0f} 秒")
    if result.duration:
        progress.log(
            f"       音频时长 {format_clock(result.duration)}，文字稿：{target.name}"
        )
    else:
        progress.log(f"       文字稿已保存：{target.name}")

    progress.report(
        "transcribe_done",
        "转写完成",
        elapsed=result.elapsed,
        segments=len(result.segments),
        duration=result.duration,
        transcript=str(target),
    )
    return result, target


# ---------------------------------------------------------------- 纪要步骤


def build_payload_preview(
    cfg: Config,
    transcript_text: str,
    stem: str,
    title: str = "",
    notes: str = "",
    provider: str | None = None,
) -> Path:
    """把将要发往云端的完整内容落盘，供发送前人工审阅。

    这是数据合规的自查手段：会议文字稿是内部信息，出网前应当能看清
    具体送出了什么。预览文件包含系统提示词与用户提示词全文。
    """
    llm = CloudLLM(cfg, provider)
    template = cfg.get("output", "template", default="supervision")
    system_prompt = prompts.get_system_prompt(template)
    user_prompt = prompts.build_user_prompt(
        transcript_text, meeting_title=title, extra_notes=notes
    )

    out_dir = cfg.path("minutes")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{stem}.prompt.md"

    body = [
        "<!--",
        "  这是「将要发送到云端」的完整内容预览，本文件不会出网。",
        f"  目标平台：{llm.label}",
        f"  接口地址：{llm.endpoint}",
        f"  使用模型：{llm.model}",
        f"  合规说明：{llm.compliance}",
        "  确认无误后，去掉 --dry-run 重新运行即可实际生成纪要。",
        "-->",
        "",
        f"## 一、系统提示词（{len(system_prompt)} 字符）",
        "",
        "```text",
        system_prompt.strip(),
        "```",
        "",
        f"## 二、用户提示词（{len(user_prompt)} 字符）",
        "",
        "```text",
        user_prompt.strip(),
        "```",
        "",
    ]
    target.write_text("\n".join(body), encoding="utf-8")
    return target


def step_summarize(
    cfg: Config,
    result: TranscriptResult,
    stem: str,
    title: str = "",
    notes: str = "",
    provider: str | None = None,
    verbose: bool = True,
    dry_run: bool = False,
    progress: Progress | None = None,
) -> tuple[Path, dict]:
    progress = progress or Progress(echo=verbose)

    if dry_run:
        target = build_payload_preview(
            cfg, result.to_prompt_text(), stem, title, notes, provider
        )
        llm = CloudLLM(cfg, provider)
        progress.log(f"[纪要] 已跳过实际调用（--dry-run），目标平台 {llm.describe()}")
        progress.log(f"       出网内容预览已保存：{target.name}")
        progress.log("       请审阅该文件，确认无误后再去掉 --dry-run 重跑。")
        return target, {"dry_run": True, "provider": provider or cfg.get("llm", "provider")}

    llm = CloudLLM(cfg, provider)

    progress.log(f"[纪要] 生成中 —— 平台 {llm.describe()}")
    progress.log(f"       数据合规提示：{llm.compliance}")
    progress.report("summarize", "生成纪要中", provider=llm.label, model=llm.model)

    body, stats = llm.summarize(
        result.to_prompt_text(),
        meeting_title=title,
        extra_notes=notes,
    )

    out_dir = cfg.path("minutes")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{stem}.minutes.md"

    header = [
        "<!--",
        f"  生成时间：{_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"  转写引擎：{result.backend} / {result.model}",
        f"  纪要平台：{llm.label} / {stats.get('model', '')}",
        f"  用量统计：输入 {stats.get('prompt_tokens', 0)} tokens，"
        f"输出 {stats.get('completion_tokens', 0)} tokens"
        + (f"，分段处理 {stats.get('chunks')} 段" if stats.get("chunked") else ""),
        f"  文字稿来源：{stem}.transcript.md",
        "  注意：纪要为机器生成，归档前须人工复核。",
        "-->",
        "",
    ]
    target.write_text("\n".join(header) + body.strip() + "\n", encoding="utf-8")

    progress.log(
        f"       完成：输入 {stats.get('prompt_tokens', 0)} tokens，"
        f"输出 {stats.get('completion_tokens', 0)} tokens"
    )
    progress.log(f"       纪要已保存：{target}")
    progress.report(
        "summarize_done",
        "纪要已生成",
        minutes=str(target),
        prompt_tokens=stats.get("prompt_tokens", 0),
        completion_tokens=stats.get("completion_tokens", 0),
    )

    return target, stats


# ---------------------------------------------------------------- 可选导出


def export_docx(cfg: Config, markdown_path: Path, verbose: bool = True) -> Path | None:
    """导出 Word 版纪要。

    使用原生 python-docx 导出而非 pandoc：pandoc 无法控制中文字体，
    导出的公文既不是仿宋也不是黑体，归档不合格。
    """
    if not bool(cfg.get("output", "export_docx", default=True)):
        return None

    markdown_path = Path(markdown_path)
    try:
        from .exporter import is_available, markdown_to_docx

        ok, note = is_available()
        if not ok:
            if verbose:
                print(f"      跳过 DOCX 导出：{note}")
            return None

        text = markdown_path.read_text(encoding="utf-8")
        target = markdown_path.with_suffix(".docx")
        markdown_to_docx(cfg, text, target)
        if verbose:
            size_kb = target.stat().st_size / 1024
            print(f"       Word 版已导出：{target.name}（{size_kb:.0f} KB）")
        return target
    except Exception as exc:
        if verbose:
            print(f"       DOCX 导出失败：{exc}")
        return None


# ---------------------------------------------------------------- 全流程


def run_pipeline(
    cfg: Config,
    audio,
    title: str = "",
    notes: str = "",
    backend: str | None = None,
    provider: str | None = None,
    skip_summary: bool = False,
    verbose: bool = True,
    dry_run: bool = False,
    form_template: Path | None = None,
    known: dict | None = None,
    progress: Progress | None = None,
) -> list[PipelineResult]:
    """执行完整流程。audio 可以是单个路径，也可以是路径列表。

    批量模式下只创建一次转写器，模型仅加载一次 —— 本机实测模型加载约 75 秒，
    逐个文件重复加载是纯粹的浪费。

    提供 form_template 或把 output.template 设为 supervision_form 时，
    走表单模式：生成结构化数据并填充单位正式模板。
    """
    progress = progress or Progress(echo=verbose)
    cfg.ensure_dirs()
    prepare_runtime_env(cfg)

    try:
        form_template = resolve_form_template(cfg, form_template)
    except FileNotFoundError as exc:
        progress.log(f"模板不可用，改用通用排版：{exc}")
        form_template = None
    form_mode = form_template is not None

    if isinstance(audio, (str, Path)):
        audios = [Path(audio)]
    else:
        audios = [Path(a) for a in audio]
    if not audios:
        raise ValueError("未提供任何音频文件。")

    batch = len(audios) > 1
    transcriber = get_transcriber(cfg, backend)

    progress.report(
        "start",
        "任务开始",
        total=len(audios),
        current=0,
        engine=transcriber.describe(),
        form_mode=form_mode,
    )

    if batch:
        progress.log(f"批量模式：共 {len(audios)} 个音频，转写模型只加载一次")
        for a in audios:
            progress.log(f"  - {a.name}")
    progress.log(f"转写引擎：{transcriber.describe()}")
    if form_mode:
        progress.log(
            f"纪要模式：填充单位正式模板"
            f"{'（' + Path(form_template).name + '）' if form_template else ''}"
        )
    progress.log("")

    results: list[PipelineResult] = []
    try:
        for idx, item in enumerate(audios, start=1):
            # 在每个文件开始时检查取消。转写是单个阻塞调用，中途无法打断，
            # 因此取消只能在此处或阶段之间生效。
            progress.check_cancel()

            if batch:
                progress.log(f"--- [{idx}/{len(audios)}] ---")

            # 只有一个音频时才套用标题，批量时按文件名区分，避免互相覆盖
            item_title = title if (title and not batch) else ""
            stem = default_stem(item, item_title)

            res = PipelineResult(audio=item)
            progress.log(f"音频：{item}")
            progress.log(f"任务标识：{stem}")
            progress.report(
                "file_start",
                f"开始处理 {item.name}",
                current=idx,
                total=len(audios),
                file=str(item),
                stem=stem,
            )

            result, res.transcript_path = step_transcribe(
                cfg, item, stem=stem, backend=backend,
                verbose=verbose, transcriber=transcriber, progress=progress,
            )
            res.transcript = result

            if skip_summary:
                results.append(res)
                progress.report(
                    "file_done", f"{item.name} 处理完成",
                    current=idx, total=len(audios), minutes=None,
                )
                progress.log("")
                continue

            # 转写已完成，进入纪要生成前再检查一次取消：
            # 此时文字稿已经落盘，取消不会白费已花掉的转写时间
            progress.check_cancel()

            if form_mode:
                res.minutes_path, res.stats = step_summarize_form(
                    cfg, result, stem=stem, provider=provider,
                    verbose=verbose, dry_run=dry_run,
                    template_path=form_template, known=known, progress=progress,
                )
            else:
                res.minutes_path, res.stats = step_summarize(
                    cfg, result, stem=stem, title=item_title, notes=notes,
                    provider=provider, verbose=verbose, dry_run=dry_run,
                    progress=progress,
                )
                if not dry_run:
                    res.docx_path = export_docx(cfg, res.minutes_path, verbose=verbose)

            results.append(res)
            progress.report(
                "file_done",
                f"{item.name} 处理完成",
                current=idx,
                total=len(audios),
                minutes=str(res.minutes_path) if res.minutes_path else "",
                docx=str(res.docx_path) if res.docx_path else "",
            )
            progress.log("")
    except CancelledError:
        progress.report(
            "cancelled",
            "已取消",
            current=len(results),
            total=len(audios),
            message=f"已取消，{len(results)} 个文件已完成并保留",
        )
        raise
    except Exception as exc:
        progress.report("error", f"处理失败：{exc}", error=str(exc))
        raise
    finally:
        # 及时归还内存，本机可用内存只有 2.5 GB
        transcriber.release()

    if skip_summary:
        progress.log("已跳过纪要生成（--skip-summary）。")

    progress.report(
        "all_done", "全部完成", total=len(audios), current=len(results)
    )
    return results


def resolve_form_template(
    cfg: Config, explicit: Path | str | None = None
) -> Path | None:
    """决定是否走模板填充模式，返回模板路径或 None。

    判定顺序：显式传入的路径 > template.enabled 开关 > output.template 取值。
    逻辑集中在这里，避免命令行与图形界面对"什么算启用了模板"给出不同答案。
    """
    if explicit not in (None, ""):
        p = Path(explicit)
        if not p.exists():
            raise FileNotFoundError(f"模板文件不存在：{p}")
        return p

    enabled = bool(cfg.get("template", "enabled", default=False))
    form_mode = str(cfg.get("output", "template", default="")).strip() == "supervision_form"
    if not (enabled or form_mode):
        return None

    configured = (cfg.get("template", "path") or "").strip()
    if not configured:
        return None
    p = Path(configured)
    if not p.exists():
        raise FileNotFoundError(f"配置中的模板文件不存在：{p}")
    return p


@dataclass
class StepOutcome:
    """分步执行时，单个输入文件的处理结果。"""

    source: Path
    outputs: list[Path] = field(default_factory=list)
    ok: bool = True
    error: str = ""
    info: dict = field(default_factory=dict)

    @property
    def primary(self) -> Path | None:
        return self.outputs[0] if self.outputs else None


def run_transcribe_step(
    cfg: Config,
    audios,
    backend: str | None = None,
    progress: Progress | None = None,
    verbose: bool = False,
) -> list[StepOutcome]:
    """第二步：录音转文字。音频 → 文字稿。

    只依赖本地能力，不需要联网。批量时转写模型只加载一次 ——
    本机实测模型加载约 100 秒，逐个文件重复加载是纯粹的浪费。

    单个文件失败不会中断整批：收集错误继续处理后面的文件，
    最后把成功与失败一并返回，由界面分别展示。
    """
    progress = progress or Progress(echo=verbose)
    cfg.ensure_dirs()
    prepare_runtime_env(cfg)

    items = [Path(a) for a in (audios if isinstance(audios, (list, tuple)) else [audios])]
    if not items:
        raise ValueError("未提供任何音频文件。")

    transcriber = get_transcriber(cfg, backend)
    outcomes: list[StepOutcome] = []

    progress.report(
        "start",
        f"开始转写 {len(items)} 个文件",
        total=len(items),
        current=0,
        engine=transcriber.describe(),
    )
    progress.log(f"转写引擎：{transcriber.describe()}")
    progress.log("")

    try:
        for idx, item in enumerate(items, start=1):
            progress.check_cancel()
            stem = default_stem(item)
            progress.log(f"--- [{idx}/{len(items)}] {item.name} ---")
            progress.report(
                "file_start", f"正在转写 {item.name}",
                current=idx, total=len(items), file=str(item), stem=stem,
            )
            try:
                result, target = step_transcribe(
                    cfg, item, stem=stem, backend=backend,
                    verbose=verbose, transcriber=transcriber, progress=progress,
                )
                outcomes.append(
                    StepOutcome(
                        source=item,
                        outputs=[target],
                        info={
                            "segments": len(result.segments),
                            "duration": result.duration,
                            "elapsed": result.elapsed,
                            "speakers": len(result.speakers),
                        },
                    )
                )
                progress.report(
                    "file_done", f"{item.name} 转写完成",
                    current=idx, total=len(items), transcript=str(target),
                )
            except Exception as exc:  # noqa: BLE001
                outcomes.append(
                    StepOutcome(source=item, outputs=[], ok=False, error=str(exc))
                )
                progress.log(f"       转写失败：{exc}")
                progress.report(
                    "file_failed", f"{item.name} 转写失败：{exc}",
                    current=idx, total=len(items), error=str(exc),
                )
            progress.log("")
    finally:
        transcriber.release()

    succeeded = sum(1 for o in outcomes if o.ok)
    progress.report(
        "all_done",
        f"转写完成：成功 {succeeded} 个，失败 {len(outcomes) - succeeded} 个",
        total=len(items), current=len(outcomes), succeeded=succeeded,
    )
    return outcomes


def run_summarize_step(
    cfg: Config,
    transcripts,
    provider: str | None = None,
    known: dict | None = None,
    form_template: Path | None = None,
    dry_run: bool = False,
    progress: Progress | None = None,
    verbose: bool = False,
) -> list[StepOutcome]:
    """第三步：文字稿生成会议纪要。文字稿 → 会议文件。

    这一步需要联网与 API Key。与转写分开之后，可以在没有网络的环境里
    先完成录音与转写，之后再联网生成纪要；也可以对同一份文字稿反复重跑
    （换模型、改提示词、补全已知信息），而不必重新转写。
    """
    progress = progress or Progress(echo=verbose)
    cfg.ensure_dirs()

    items = [
        Path(t) for t in (transcripts if isinstance(transcripts, (list, tuple)) else [transcripts])
    ]
    if not items:
        raise ValueError("未提供任何文字稿。")

    try:
        template_path = resolve_form_template(cfg, form_template)
    except FileNotFoundError as exc:
        # 模板丢了不该让整批任务失败，退回通用排版并明确告知
        progress.log(f"       模板不可用，本次改用通用排版：{exc}")
        template_path = None
    form_mode = template_path is not None

    outcomes: list[StepOutcome] = []
    progress.report("start", f"开始生成 {len(items)} 份纪要", total=len(items), current=0)
    progress.log("")
    if form_mode:
        progress.log(
            f"纪要模式：填充单位模板"
            f"{'（' + template_path.name + '）' if template_path else ''}"
        )
        progress.log("")

    for idx, item in enumerate(items, start=1):
        progress.check_cancel()
        stem = item.name.replace(".transcript.md", "")
        progress.log(f"--- [{idx}/{len(items)}] {item.name} ---")
        progress.report(
            "file_start", f"正在生成 {item.name} 的纪要",
            current=idx, total=len(items), file=str(item), stem=stem,
        )
        try:
            target, stats = summarize_existing(
                cfg, item, provider=provider, verbose=verbose, dry_run=dry_run,
                form_template=template_path if form_mode else None,
                known=known, progress=progress,
            )
            outputs = [target]
            if target.suffix == ".docx":
                md = target.with_suffix(".md")
                js = target.with_suffix(".json")
                outputs += [p for p in (md, js) if p.exists()]
            outcomes.append(
                StepOutcome(source=item, outputs=outputs, info=dict(stats or {}))
            )
            progress.report(
                "file_done", f"{item.name} 纪要完成",
                current=idx, total=len(items),
                minutes=str(target), outputs=[str(p) for p in outputs],
            )
        except Exception as exc:  # noqa: BLE001
            outcomes.append(StepOutcome(source=item, outputs=[], ok=False, error=str(exc)))
            progress.log(f"       生成失败：{exc}")
            progress.report(
                "file_failed", f"{item.name} 生成失败：{exc}",
                current=idx, total=len(items), error=str(exc),
            )
        progress.log("")

    succeeded = sum(1 for o in outcomes if o.ok)
    progress.report(
        "all_done",
        f"纪要生成完成：成功 {succeeded} 份，失败 {len(outcomes) - succeeded} 份",
        total=len(items), current=len(outcomes), succeeded=succeeded,
    )
    return outcomes


_STAMP_RE = __import__("re").compile(
    r"^\*\*\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]\*\*\s*(?:\*\*(.+?)\*\*)?\s*$"
)


def load_transcript(path: Path) -> TranscriptResult:
    """从已生成的文字稿 Markdown 还原成 TranscriptResult。

    这样 summarize 命令就能复用与 run 完全相同的纪要生成逻辑，
    不必为"重新生成纪要"维护第二套代码路径。
    """
    import re as _re

    path = Path(path)
    text = path.read_text(encoding="utf-8")

    backend, model = "", ""
    duration = None
    elapsed = 0.0
    for line in text.splitlines()[:16]:
        if line.startswith("- 转写引擎："):
            value = line.split("：", 1)[1].strip()
            if "/" in value:
                backend, model = [x.strip() for x in value.split("/", 1)]
            else:
                backend = value
        elif line.startswith("- 音频时长："):
            duration = _parse_clock(line.split("：", 1)[1].strip())
        elif line.startswith("- 处理耗时："):
            # 形如 17:06（含模型加载的固定开销）
            raw = line.split("：", 1)[1].strip()
            elapsed = _parse_clock(raw.split("（")[0].strip()) or 0.0

    segments: list[Segment] = []
    current: Segment | None = None
    for raw in text.splitlines():
        line = raw.strip()
        m = _STAMP_RE.match(line)
        if m:
            if current is not None and current.text.strip():
                segments.append(current)
            h, mnt, sec, speaker = m.groups()
            if sec is not None:
                seconds = int(h) * 3600 + int(mnt) * 60 + int(sec)
            else:
                seconds = int(h) * 60 + int(mnt)
            current = Segment(text="", start=float(seconds), end=0.0, speaker=speaker or "")
            continue
        if current is None:
            continue
        if not line or line == "---" or line.startswith("#") or line.startswith("- "):
            continue
        current.text = (current.text + " " + line).strip() if current.text else line

    if current is not None and current.text.strip():
        segments.append(current)

    return TranscriptResult(
        segments=segments,
        backend=backend or "(未知)",
        model=model or path.name,
        audio=str(path),
        diarized=any(s.speaker for s in segments),
        duration=duration,
        elapsed=elapsed,
    )


def _parse_clock(text: str) -> float | None:
    """把 mm:ss 或 hh:mm:ss 解析成秒。"""
    parts = (text or "").strip().split(":")
    if not all(p.isdigit() for p in parts) or len(parts) < 2:
        return None
    parts = [int(p) for p in parts]
    if len(parts) == 3:
        return float(parts[0] * 3600 + parts[1] * 60 + parts[2])
    return float(parts[0] * 60 + parts[1])


def summarize_existing(
    cfg: Config,
    transcript_path: Path,
    title: str = "",
    notes: str = "",
    provider: str | None = None,
    verbose: bool = True,
    dry_run: bool = False,
    form_template: Path | None = None,
    known: dict | None = None,
    progress: Progress | None = None,
) -> tuple[Path, dict]:
    """对已生成的文字稿重跑纪要，不重新转写。支持模板填充模式。

    转写一次可能要几十分钟，而纪要部分经常需要反复调整（换模型、改提示词、
    补全已知信息）。把这两件事解耦，重跑纪要就不必再付转写的代价。
    """
    progress = progress or Progress(echo=verbose)
    transcript_path = Path(transcript_path)
    if not transcript_path.exists():
        raise FileNotFoundError(f"文字稿不存在：{transcript_path}")

    cfg.ensure_dirs()
    stem = transcript_path.name.replace(".transcript.md", "")
    result = load_transcript(transcript_path)

    # 模板模式的判定交给调用方（run_summarize_step / CLI 都已用
    # resolve_form_template 解析过），这里只按传入的路径决定走哪条分支，
    # 避免两处判定逻辑不一致。
    if form_template is not None:
        return step_summarize_form(
            cfg, result, stem=stem, provider=provider, verbose=verbose,
            dry_run=dry_run, template_path=form_template, known=known,
            progress=progress,
        )

    text = transcript_path.read_text(encoding="utf-8")
    body = _strip_markdown_scaffold(text)

    if dry_run:
        target = build_payload_preview(cfg, body, stem, title, notes, provider)
        llm = CloudLLM(cfg, provider)
        progress.log(f"[纪要] 已跳过实际调用（--dry-run），目标平台 {llm.describe()}")
        progress.log(f"       出网内容预览已保存：{target.name}")
        return target, {"dry_run": True, "provider": provider or cfg.get("llm", "provider")}

    llm = CloudLLM(cfg, provider)
    progress.log(f"[纪要] 生成中 —— 平台 {llm.describe()}")
    progress.log(f"       数据合规提示：{llm.compliance}")
    progress.report("summarize", "生成纪要中", provider=llm.label, model=llm.model)

    minutes, stats = llm.summarize(body, meeting_title=title, extra_notes=notes)

    out_dir = cfg.path("minutes")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{stem}.minutes.md"
    target.write_text(minutes.strip() + "\n", encoding="utf-8")
    progress.log(f"       纪要已保存：{target.name}")
    progress.report("summarize_done", "纪要已生成", minutes=str(target))
    return target, stats


def _strip_markdown_scaffold(text: str) -> str:
    """去掉转写稿的元信息头与标题行，只保留正文供大模型消费。"""
    lines = text.splitlines()
    body: list[str] = []
    in_meta = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("<!--"):
            in_meta = True
        if in_meta:
            if stripped.endswith("-->"):
                in_meta = False
            continue
        if stripped.startswith("#"):
            continue
        if not stripped:
            continue
        if stripped.startswith("- ") and ("引擎" in line or "音频" in line or "耗时" in line
                                          or "时长" in line or "分离" in line or "速率" in line):
            continue
        if stripped == "---":
            continue
        body.append(line)
    return "\n".join(body)
