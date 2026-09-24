#!/usr/bin/env python
"""会议纪要工具命令行入口。

用法速查：
  python mm.py check                     环境自检（建议首次运行）
  python mm.py providers                 查看云端平台与合规说明
  python mm.py backends                  查看转写引擎可用性
  python mm.py record                    开始录音（Ctrl+C 结束）
  python mm.py run 录音.wav              全流程：转写 + 生成纪要
  python mm.py transcribe 录音.wav       只转写
  python mm.py summarize 文字稿.md       对已有文字稿重跑纪要

切换引擎或平台无需改代码：
  python mm.py run 录音.wav --backend faster_whisper
  python mm.py run 录音.wav --provider aliyun_bailian
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import shutil
import sys
from pathlib import Path

# Windows 控制台默认可能是 GBK，中文输出会乱码，这里强制切到 UTF-8
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mmtools.config import Config, ConfigError, PROJECT_ROOT  # noqa: E402
from mmtools import pipeline as pipe  # noqa: E402
from mmtools.summarizer import LLMError  # noqa: E402

LINE = "=" * 62


def _load(args) -> Config:
    return Config.load(getattr(args, "config", None))


def _banner(text: str) -> None:
    print(LINE)
    print(text)
    print(LINE)


# ---------------------------------------------------------------- check


def cmd_check(args) -> int:
    cfg = _load(args)
    _banner("环境自检")

    print(f"项目目录：{cfg.project_dir}")
    print(f"配置文件：{PROJECT_ROOT / 'config.yaml'}"
          f"{'（已叠加 config.local.yaml）' if cfg.local_loaded else ''}")
    print("")

    # --- 目录与磁盘 ---
    print("[目录]")
    try:
        created = cfg.ensure_dirs()
        for p in created:
            print(f"  {p}")
    except Exception as exc:
        print(f"  创建目录失败：{exc}")
        return 2

    print("")
    print("[磁盘剩余空间]")
    for drive in ("C:\\", "D:\\"):
        try:
            usage = shutil.disk_usage(drive)
            free_gb = usage.free / 1024 ** 3
            flag = ""
            if drive.startswith("C") and free_gb < 10:
                flag = "  ← 空间紧张，模型与缓存务必留在 D 盘"
            print(f"  {drive} 剩余 {free_gb:.1f} GB{flag}")
        except Exception:
            pass

    # --- 转写引擎 ---
    print("")
    print("[转写引擎]")
    from mmtools.transcribers import available_backends, get_transcriber

    backends = available_backends()
    active = cfg.backend_name
    for name, (ok, note) in backends.items():
        mark = "✓" if ok else "✗"
        current = " ← 当前选用" if name == active else ""
        print(f"  {mark} {name}: {note}{current}")
    if not backends.get(active, (False, ""))[0]:
        print(f"  提示：当前选用的 {active} 依赖未就绪，请先安装依赖或改用其他引擎。")

    if backends.get(active, (False, ""))[0]:
        try:
            tr = get_transcriber(cfg)
            conf = tr.conf
            print(f"  配置：模型 {conf.get('model')}"
                  f"{'，说话人分离 ' + str(conf.get('spk_model')) if conf.get('spk_model') else ''}"
                  f"，设备 {conf.get('device', 'cpu')}")
            if not (conf.get("hotword") or "").strip():
                print("  提示：hotword 为空。若项目名、单位名识别不准，可在 config.yaml 中填入热词。")
        except Exception as exc:
            print(f"  配置读取失败：{exc}")

    # --- 录音 ---
    print("")
    print("[录音模块]")
    from mmtools.recorder import check_recording_ready

    ok, note = check_recording_ready()
    print(f"  {'✓' if ok else '✗'} {note}")

    # --- 云端平台 ---
    print("")
    print("[云端平台]")
    try:
        key, prov = cfg.provider(args.provider)
        source = cfg.api_key_source(args.provider)
        token = cfg.api_key(args.provider)
        print(f"  当前平台：{prov.get('label', key)}（{key}）")
        print(f"  接口地址：{prov.get('base_url')}")
        print(f"  模型名称：{prov.get('model')}")
        print(f"  API Key ：{source}")
        if token:
            print(f"  密钥(脱敏)：{cfg.mask_secret(token)}")
        else:
            print("  ⚠ 尚未配置密钥，无法生成纪要。配置方法见 README 第二节。")
        print(f"  合规说明：{prov.get('compliance', '（未注明）')}")
    except ConfigError as exc:
        print(f"  {exc}")
        return 2

    # --- 纪要模板 ---
    print("")
    print("[纪要模板]")
    from mmtools.prompts import TEMPLATES, get_system_prompt

    template = cfg.get("output", "template", default="supervision")
    try:
        body = get_system_prompt(template)
        print(f"  当前模板：{template}（{len(body)} 字符）")
        print(f"  内置模板：{'、'.join(TEMPLATES)}")
    except KeyError as exc:
        print(f"  {exc}")

    # --- 可选在线探测 ---
    if getattr(args, "online", False):
        print("")
        print("[在线连通性测试]")
        from mmtools.summarizer import CloudLLM

        try:
            llm = CloudLLM(cfg, args.provider)
            reply = llm.probe()
            print(f"  ✓ 调用成功，模型回复：{reply[:40]}")
        except (LLMError, ConfigError) as exc:
            print(f"  ✗ {exc}")
            return 3
    else:
        print("")
        print("提示：加 --online 可实际调用一次云端接口，验证密钥与网络连通性。")

    print("")
    print("自检结束。")
    return 0


# ---------------------------------------------------------------- providers


def cmd_providers(args) -> int:
    cfg = _load(args)
    _banner("云端平台清单（全部走 OpenAI 兼容协议）")
    print("切换平台：修改 config.yaml 的 llm.provider，或运行时加 --provider <名称>")
    print("")

    current = cfg.get("llm", "provider")
    for name, prov in cfg.providers().items():
        marker = " ●" if name == current else "  "
        print(f"{marker} {name}  ——  {prov.get('label', '')}")
        print(f"     接口：{prov.get('base_url') or '（未配置，需自行填写）'}")
        print(f"     模型：{prov.get('model') or '（未配置）'}")
        print(f"     密钥来源：{cfg.api_key_source(name)}")
        print(f"     合规：{prov.get('compliance', '（未注明）')}")
        if prov.get("note"):
            print(f"     备注：{prov['note']}")
        print("")

    print("新增平台：在 config.yaml 的 providers 段追加一段配置即可，无需改动代码。")
    print("")
    print("重要提醒：必须使用开放平台 API，不要使用网页版或 App。")
    print("同一厂商的网页端与 API 端数据条款完全不同，网页端通常默认参与模型优化。")
    print("另需注意：承诺不训练不等于零留存，涉密会议内容不要送入任何公网大模型。")
    return 0


# ---------------------------------------------------------------- backends


def cmd_backends(args) -> int:
    cfg = _load(args)
    _banner("转写引擎清单")
    from mmtools.transcribers import available_backends

    current = cfg.backend_name
    for name, (ok, note) in available_backends().items():
        marker = " ●" if name == current else "  "
        print(f"{marker} {name}  {'可用' if ok else '不可用'}")
        conf = cfg.backend_conf(name)
        if conf:
            for k, v in conf.items():
                if v not in ("", None):
                    print(f"      {k}: {v}")
        print(f"      状态：{note}")
        print("")

    print("切换引擎：修改 config.yaml 的 transcription.backend，或运行时加 --backend <名称>")
    print("")
    print(f"当前选用：{current}")
    return 0


# ---------------------------------------------------------------- devices


def cmd_devices(args) -> int:
    _load(args)
    _banner("音频设备清单")
    from mmtools.recorder import RecorderError, list_devices

    try:
        print(list_devices())
    except RecorderError as exc:
        print(f"读取设备失败：{exc}")
        return 4
    print("")
    print("录音时关注两点：")
    print("  1. 是否存在带 loopback 标记的设备 —— 它用于录制系统音频（会议对方）")
    print("  2. 默认输入设备是否为本机麦克风")
    return 0


# ---------------------------------------------------------------- fill


def cmd_fill(args) -> int:
    """用手工修订过的 JSON 重新填充模板，不再调用云端。

    实测中录音里的参会人员姓名、机构全称几乎必然识别错误 —— 这类信息只能
    人工补齐。补齐后如果重新生成一次纪要，既费钱又可能把已经改好的内容
    再改回去。因此提供这条命令：把 .minutes.json 改好，直接重新填充模板。
    """
    cfg = _load(args)
    _banner("从结构化数据重新填充模板")

    json_path = Path(args.data)
    if not json_path.exists():
        print(f"数据文件不存在：{json_path}")
        return 2

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"JSON 解析失败：{exc}")
        print("提示：请检查文件是否为合法 JSON（注意逗号、引号、括号是否配对）。")
        return 2
    if not isinstance(data, dict):
        print("数据文件的顶层结构必须是 JSON 对象。")
        return 2

    # 与云端输出走同一套规整逻辑，避免手改后字段类型不一致
    from mmtools.summarizer import normalize_form_data

    data = normalize_form_data(data)

    template_value = args.template or (cfg.get("template", "path") or "").strip()
    if not template_value:
        print("未指定模板路径。可用 --template 指定，或在 config.yaml 的 template.path 中配置。")
        return 2
    template = Path(template_value)
    if not template.exists():
        print(f"模板文件不存在：{template}")
        return 2

    out = Path(args.output) if args.output else json_path.with_suffix("").with_suffix(".docx")
    from mmtools.docx_template import fill_official_minutes

    fill_official_minutes(cfg, template, out, data)

    print(f"模板：{template.name}")
    print(f"输出：{out}")
    print("")
    print(f"议定事项 {len(data.get('items') or [])} 条，"
          f"参会单位 {len(data.get('attendees') or [])} 个")
    print("未调用任何云端接口。")
    return 0


# ---------------------------------------------------------------- merge


def cmd_merge(args) -> int:
    cfg = _load(args)
    from mmtools.pipeline import load_transcript
    from mmtools.transcribers import merge_segments

    _banner("合并文字稿段落")

    path = Path(args.transcript)
    if not path.exists():
        print(f"文字稿不存在：{path}")
        return 2

    conf = cfg.get("transcription", "merge") or {}
    if not conf.get("enabled", True):
        print("段落合并在配置中被关闭（transcription.merge.enabled）。")
        return 0

    result = load_transcript(path)
    before = len(result.segments)
    if before <= 1:
        print("文字稿为空或只有一段，无需合并。")
        return 0

    merged = merge_segments(
        result.segments,
        max_gap=float(conf.get("max_gap_s", 4.0)),
        max_duration=float(conf.get("max_duration_s", 90)),
        max_chars=int(conf.get("max_chars", 400)),
    )
    after = len(merged)

    if after >= before:
        print(f"没有可合并的相邻段落（{before} 段保持不变）。")
        return 0

    # 先备份，避免合并参数不合适时丢掉原始文字稿
    backup = path.with_suffix(".before-merge.md")
    if not backup.exists():
        backup.write_bytes(path.read_bytes())

    result.segments = merged
    result.meta = {**result.meta, "merge": {"before": before, "after": after}}
    path.write_text(result.to_markdown(path.name.replace(".transcript.md", ".wav")), encoding="utf-8")

    print(f"合并完成：{before} 段 → {after} 段")
    print(f"原文字稿已备份为：{backup.name}")
    print(f"已更新：{path.name}")
    print("")
    print("说明：合并只影响排版与给大模型的输入，不改变时间戳范围。")
    return 0


# ---------------------------------------------------------------- glossary


def cmd_glossary(args) -> int:
    cfg = _load(args)
    from mmtools.glossary import build_rules, describe_stats

    _banner("术语规范化")

    conf = cfg.get("transcription", "glossary") or {}
    enabled = bool(conf.get("enabled", False))
    rules = build_rules(conf)

    print(f"当前状态：{'已启用' if enabled else '未启用（config.yaml 的 transcription.glossary.enabled）'}")
    print(f"规则条数：{len(rules)}")
    print("")

    if not rules:
        print("尚未配置任何规则。")
        print("")
        print("配置位置：config.yaml 的 transcription.glossary")
        print("注意：盲目替换会破坏正常语句 —— 例如把\"建立\"改成\"监理\"，")
        print("      会让\"建立制度\"这类合法表述出错。请只针对实际观察到的误识别配置。")
        return 0

    print("规则清单：")
    for i, rule in enumerate(rules, start=1):
        kind = "正则" if rule.is_regex else "字面"
        print(f"  {i:>2}. [{kind}] {rule.label}")
    print("")

    if not args.test:
        print("预演：加 --test <文字稿.md> 可以查看规则实际会改动哪些内容，不改动任何文件。")
        return 0

    # 预演模式
    from mmtools.pipeline import _strip_markdown_scaffold

    path = Path(args.test)
    if not path.exists():
        print(f"文字稿不存在：{path}")
        return 2

    text = _strip_markdown_scaffold(path.read_text(encoding="utf-8"))
    print("-" * 62)
    print(f"预演对象：{path.name}（{len(text)} 字符）")
    print("-" * 62)
    print("")

    total = 0
    for rule in rules:
        matches = list(rule.pattern.finditer(text))
        if not matches:
            continue
        total += len(matches)
        print(f"  规则 {rule.label} —— 命中 {len(matches)} 处")
        for m in matches[:3]:
            start = max(0, m.start() - 12)
            end = min(len(text), m.end() + 12)
            before = text[start:end].replace("\n", " ")
            after = text[start:m.start()] + rule.target + text[m.end():end]
            after = after.replace("\n", " ")
            print(f"      改前：……{before}……")
            print(f"      改后：……{after}……")
        if len(matches) > 3:
            print(f"      （另有 {len(matches) - 3} 处未展示）")
        print("")

    print("-" * 62)
    if total == 0:
        print("本次预演没有任何改动。规则已配置但未命中当前文字稿。")
    else:
        print(f"合计将改动 {total} 处。")
        print("请逐条核对上面的前后对照，确认没有改坏正常语句。")
        if not enabled:
            print("")
            print("确认无误后，把 config.yaml 的 transcription.glossary.enabled 改为 true 即可生效。")
    return 0


# ---------------------------------------------------------------- record


def cmd_record(args) -> int:
    cfg = _load(args)
    cfg.ensure_dirs()

    stem = args.name or _dt.datetime.now().strftime("%Y-%m-%d_%H%M") + "_会议录音"
    output = cfg.path("audio") / f"{pipe.slugify(stem)}.wav"

    _banner("会议录音")
    print("录制内容：系统音频（会议对方）+ 本机麦克风，混音为 16 kHz 单声道")
    print(f"输出文件：{output}")
    print("")
    print("按 Ctrl+C 结束录音。请确保已获得参会人知情同意。")
    print("")

    from mmtools.recorder import RecorderError, record_session

    started = _dt.datetime.now()

    def on_tick(elapsed: float, samples: int) -> None:
        mins, secs = divmod(int(elapsed), 60)
        print(f"\r  已录制 {mins:02d}:{secs:02d}", end="", flush=True)

    try:
        path = record_session(
            cfg,
            output,
            duration=args.duration,
            on_tick=on_tick,
        )
    except RecorderError as exc:
        print("")
        print(f"录音失败：{exc}")
        return 4

    print("")
    if not path.exists() or path.stat().st_size <= 44:
        print("未录制到任何音频，请检查音频设备与系统声音输出是否正常。")
        return 4

    size_mb = path.stat().st_size / 1024 ** 2
    print(f"录音已保存：{path}（{size_mb:.1f} MB）")

    if args.auto_run:
        print("")
        print("录音结束，直接进入转写与纪要生成……")
        args.audio = [str(path)]
        return cmd_run(args)
    return 0


# ---------------------------------------------------------------- transcribe


def cmd_transcribe(args) -> int:
    cfg = _load(args)
    _banner("转写")
    try:
        result, target = pipe.step_transcribe(
            cfg,
            Path(args.audio),
            backend=args.backend,
        )
    except Exception as exc:
        print(f"转写失败：{exc}")
        return 5
    print("")
    print(f"说话人：{'、'.join(result.speakers) if result.speakers else '未启用或未识别'}")
    return 0


# ---------------------------------------------------------------- summarize


def cmd_summarize(args) -> int:
    cfg = _load(args)
    _banner("生成纪要")

    try:
        form_template = _resolve_form_template(args, cfg)
    except ConfigError as exc:
        print(f"配置错误：{exc}")
        return 2
    known = _known_facts(args, cfg)

    if form_template:
        print(f"模板填充模式：{form_template.name}")

    try:
        target, stats = pipe.summarize_existing(
            cfg,
            Path(args.transcript),
            title=args.title or "",
            notes=args.notes or "",
            provider=args.provider,
            dry_run=getattr(args, "dry_run", False),
            form_template=form_template,
            known=known,
        )
    except (LLMError, ConfigError, FileNotFoundError) as exc:
        print(f"生成失败：{exc}")
        return 6

    if not getattr(args, "dry_run", False) and not form_template:
        pipe.export_docx(cfg, target)
    return 0


# ---------------------------------------------------------------- run


def cmd_run(args) -> int:
    cfg = _load(args)
    audios = args.audio if isinstance(args.audio, list) else [args.audio]

    try:
        form_template = _resolve_form_template(args, cfg)
    except ConfigError as exc:
        print(f"配置错误：{exc}")
        return 2
    known = _known_facts(args, cfg)

    _banner(
        f"全流程：转写 + 生成纪要（{len(audios)} 个音频）"
        if len(audios) > 1
        else "全流程：转写 + 生成纪要"
    )
    if form_template:
        print(f"模板填充模式：{form_template.name}")
        provided = [k for k, v in known.items() if v]
        if provided:
            print(f"已知信息：{len(provided)} 项（{', '.join(provided)}）")
        else:
            print("提示：未提供项目名称与单位全称，这些字段可能填成（文字稿未提及）。")
            print("      建议用 --project / --owner-unit / --builder-unit / --supervisor-unit 补全。")
        print("")

    try:
        results = pipe.run_pipeline(
            cfg,
            audios,
            title=args.title or "",
            notes=args.notes or "",
            backend=args.backend,
            provider=args.provider,
            skip_summary=getattr(args, "skip_summary", False),
            dry_run=getattr(args, "dry_run", False),
            form_template=form_template,
            known=known,
        )
    except (LLMError, ConfigError, FileNotFoundError, RuntimeError, ValueError) as exc:
        print("")
        print(f"执行失败：{exc}")
        return 6

    print(LINE)
    print("全部完成" if len(results) > 1 else "完成")
    print("")
    for res in results:
        print(f"  音频：{res.audio.name}")
        if res.transcript is not None and res.transcript.duration:
            from mmtools.transcribers import format_clock

            print(f"    时长 {format_clock(res.transcript.duration)}，"
                  f"转写耗时 {format_clock(res.transcript.elapsed)}")
        if res.transcript_path:
            print(f"    文字稿：{res.transcript_path}")
        if res.minutes_path:
            label = "出网预览" if args.dry_run else "会议纪要"
            print(f"    {label}：{res.minutes_path}")
        if res.docx_path:
            print(f"    Word 版：{res.docx_path}")
        print("")

    if args.dry_run:
        print("这是预览模式，未实际调用云端接口。")
        print("请审阅预览文件，确认无误后去掉 --dry-run 重新运行。")
    else:
        print("提醒：纪要为机器生成，归档前请人工复核。")
    return 0


# ---------------------------------------------------------------- 入口


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    """把通用选项挂到每个子命令上。

    必须挂在子命令上，`mm.py run x.wav --backend faster_whisper` 这种自然语序才成立。
    若只放在顶层解析器，用户就得写成 `mm.py --backend ... run x.wav`，与直觉不符 ——
    这是实测中发现的易用性缺陷。
    """
    parser.add_argument("--config", help="指定配置文件路径（默认项目根目录的 config.yaml）")
    parser.add_argument("--provider", help="覆盖云端平台（见 providers 命令）")
    parser.add_argument("--backend", help="覆盖转写引擎（funasr / faster_whisper）")


def _add_form_options(parser: argparse.ArgumentParser) -> None:
    """表单模式：按单位正式模板生成纪要。

    项目名称、单位全称这类事实由用户提供，而不是让模型从文字稿里推断 ——
    模型猜错的单位全称会直接写进正式归档文件，代价很高。
    """
    g = parser.add_argument_group("模板填充（可选）")
    g.add_argument("--form", metavar="模板路径",
                   help="按单位正式模板生成纪要，传入 .docx 模板的绝对路径")
    g.add_argument("--project", help="项目名称（已知事实）")
    g.add_argument("--owner-unit", help="建设单位全称")
    g.add_argument("--builder-unit", help="承建单位全称")
    g.add_argument("--supervisor-unit", help="监理单位全称")
    g.add_argument("--location", help="会议地点")
    g.add_argument("--meeting-no", help="会议次数，例如 第3次")


def _known_facts(args, cfg) -> dict:
    """合并配置文件与命令行提供的已知信息，命令行优先。"""
    known = dict(cfg.get("template", "known") or {})
    for key in ("project", "owner_unit", "builder_unit", "supervisor_unit",
                "location", "meeting_no"):
        value = getattr(args, key, None)
        if value:
            known[key] = value
    if getattr(args, "topic", None):
        known["topic"] = args.topic
    notes = getattr(args, "notes", None)
    if notes:
        known["notes"] = notes
    return known


def _resolve_form_template(args, cfg):
    """决定是否走表单模式，返回模板路径或 None。"""
    value = getattr(args, "form", None)
    if value:
        p = Path(value)
        if not p.exists():
            raise ConfigError(f"模板文件不存在：{p}")
        return p
    if bool(cfg.get("template", "enabled", default=False)) or \
            str(cfg.get("output", "template", default="")).strip() == "supervision_form":
        configured = (cfg.get("template", "path") or "").strip()
        if configured:
            p = Path(configured)
            if not p.exists():
                raise ConfigError(f"配置中的模板文件不存在：{p}")
            return p
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mm.py",
        description="会议纪要工具：本地转写 + 可配置云端纪要",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="环境自检")
    p.add_argument("--online", action="store_true", help="额外做一次真实的云端调用测试")
    _add_common_options(p)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("providers", help="查看云端平台清单与合规说明")
    _add_common_options(p)
    p.set_defaults(func=cmd_providers)

    p = sub.add_parser("backends", help="查看转写引擎可用性")
    _add_common_options(p)
    p.set_defaults(func=cmd_backends)

    p = sub.add_parser("devices", help="列出音频设备（排查录音问题时用）")
    _add_common_options(p)
    p.set_defaults(func=cmd_devices)

    p = sub.add_parser("fill", help="用手工修订过的 JSON 重新填充模板（不调用云端）")
    p.add_argument("data", help="结构化数据 .minutes.json 文件路径")
    p.add_argument("--template", help="模板路径（默认取 config.yaml 的 template.path）")
    p.add_argument("--output", help="输出 .docx 路径（默认与数据文件同名）")
    _add_common_options(p)
    p.set_defaults(func=cmd_fill)

    p = sub.add_parser("merge", help="合并已有文字稿中同说话人的碎片段落")
    p.add_argument("transcript", help="文字稿 .md 文件路径")
    _add_common_options(p)
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser("glossary", help="查看术语规范化规则，并可预演改动效果")
    p.add_argument("--test", help="对指定文字稿预演规则效果，不改动任何文件")
    _add_common_options(p)
    p.set_defaults(func=cmd_glossary)

    p = sub.add_parser("record", help="录制会议音频")
    p.add_argument("--name", help="录音文件名（默认按时间戳命名）")
    p.add_argument("--duration", type=float, help="录制指定秒数后自动停止")
    p.add_argument("--auto-run", action="store_true", help="录音结束后立即转写并生成纪要")
    p.add_argument("--title", help="会议名称（配合 --auto-run 使用）")
    p.add_argument("--notes", help="补充说明（配合 --auto-run 使用）")
    p.add_argument("--dry-run", action="store_true", help="配合 --auto-run：只导出出网内容预览")
    _add_common_options(p)
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("transcribe", help="仅转写音频")
    p.add_argument("audio", help="音频文件路径")
    _add_common_options(p)
    p.set_defaults(func=cmd_transcribe)

    p = sub.add_parser("summarize", help="对已有文字稿生成纪要")
    p.add_argument("transcript", help="文字稿 .md 文件路径")
    p.add_argument("--title", help="会议名称，用于填充纪要抬头")
    p.add_argument("--notes", help="补充说明，例如参会单位、会议背景")
    p.add_argument("--dry-run", action="store_true",
                   help="只导出将要发送到云端的内容供审阅，不实际调用接口")
    _add_form_options(p)
    _add_common_options(p)
    p.set_defaults(func=cmd_summarize)

    p = sub.add_parser("run", help="全流程：转写 + 生成纪要")
    p.add_argument("audio", nargs="+", help="音频文件路径，可一次给多个（批量只加载一次模型）")
    p.add_argument("--title", help="会议名称（仅在单个音频时生效）")
    p.add_argument("--notes", help="补充说明")
    p.add_argument("--skip-summary", action="store_true", help="只转写，不调用云端")
    p.add_argument("--dry-run", action="store_true",
                   help="只导出将要发送到云端的内容供审阅，不实际调用接口")
    _add_form_options(p)
    _add_common_options(p)
    p.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print("")
        print(f"配置错误：{exc}")
        return 2
    except KeyboardInterrupt:
        print("")
        print("已中断。")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
