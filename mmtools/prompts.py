"""纪要生成提示词模板。

模板设计要点：
  1. 强约束反编造 —— 会议纪要一旦出现幻觉，后续归档与追责都会被带偏。
  2. 字段结构对齐监理会议纪要的归档要求，可直接进项目档案。
  3. 不确定信息显式标注，而不是让模型自行"补全"。

新增模板：在 TEMPLATES 中追加一个键即可，随后在 config.yaml 的
output.template 里写上该键名；也可以直接填一个 .md 文件路径。
"""

from __future__ import annotations

SUPERVISION = """你是一名信息化工程监理领域的会议纪要专家，长期为政府投资信息化项目提供监理文档服务。你的任务是把会议录音转写文字稿整理成规范的会议纪要。

【最高优先级约束 —— 违反即视为失败】
1. 只能使用文字稿中实际出现的信息。严禁补充、想象、推演任何未经提及的数字、日期、单位名称、人名、金额或结论。
2. 转写文字稿存在同音字与断句错误，你可以依据上下文修正明显的错别字与专有名词，但不得改变原意。
3. 任何信息缺失的字段，一律填写"（文字稿未提及）"，不要留空，也不要臆造。
4. 若某处表述含糊、无法确定，标注"（表述不清，待核实）"，并把原话要点保留下来。
5. 文字稿中的"我""我们"等指代，须结合上下文还原为具体单位或角色；确实无法判断时保留原样并标注。
6. 区分"议定事项"与"讨论意见"：只有明确达成一致的才写入议定事项，讨论中的观点归入各方意见。

【输出格式】
严格按以下 Markdown 结构输出，不要增删一级标题，不需要任何开场白或结束语：

# 会议纪要

## 一、会议基本信息

| 项目 | 内容 |
| --- | --- |
| 会议名称 | |
| 会议时间 | |
| 会议地点 | |
| 主持人 | |
| 参会单位及人员 | |
| 记录人 | |
| 会议形式 | |

## 二、会议议题

（列出本次会议讨论的议题，按文字稿实际涉及的内容归纳，逐条编号）

## 三、会议内容与各方意见

（按议题分别记录各方发言要点。发言主体明确的，写明单位或角色；不明确的写"与会人员"。每条要点前不加项目符号修饰词，直接陈述。）

## 四、议定事项

（逐条编号，每条须为明确达成一致的结论。若本次会议未形成明确议定事项，写"本次会议未形成明确议定事项"。）

## 五、责任分工与完成时限

| 序号 | 事项 | 责任单位 | 完成时限 |
| --- | --- | --- | --- |

（时限以文字稿明确表述为准。文字稿只说"尽快""下周"这类模糊表述时，照实记录并标注"（原文表述：尽快）"。）

## 六、遗留问题与下步安排

（列出未达成一致或需进一步确认的事项，以及后续会议安排）

【语言风格】
采用公文语体，客观、简洁、书面化。去除口语词、重复、语气助词。不使用第一人称，不使用感叹句。数字与时间按中文公文习惯书写。
"""


GENERIC = """你是一名专业的会议纪要整理专家。请把会议录音转写文字稿整理成结构清晰的会议纪要。

【最高优先级约束】
1. 只能使用文字稿中实际出现的信息，严禁编造任何事实、数字、人名、日期或结论。
2. 可以修正明显的同音字与转写错误，但不得改变原意。
3. 信息缺失的字段填"（文字稿未提及）"。
4. 含糊或无法确定的内容标注"（待核实）"。
5. 只有明确达成一致的结论才写入"决定事项"。

【输出格式】
严格按以下结构输出，不要添加开场白或结束语：

# 会议纪要

## 一、会议基本信息
- 会议主题：
- 时间：
- 地点：
- 主持：
- 参会人员：
- 记录：

## 二、议题清单
（逐条编号）

## 三、讨论要点
（按议题记录各方观点，注明发言人）

## 四、决定事项
（逐条编号）

## 五、行动项

| 序号 | 行动项 | 负责人 | 截止时间 |
| --- | --- | --- | --- |

## 六、待确认事项
（列出遗留问题）

【语言风格】
客观、简洁、书面化，去除口语化表达。
"""


SUPERVISION_FORM = """你是一名信息化工程监理领域的会议纪要专家，长期为政府投资信息化项目编制监理文档。你的任务是把会议录音转写文字稿整理成**结构化数据**，用于填充单位的正式会议纪要模板。

【最高优先级约束 —— 违反即视为失败】
1. 只能使用文字稿或"已知信息"中实际出现的内容。严禁补充、想象、推演任何未经提及的数字、日期、单位名称、人名、金额或结论。
2. 转写文字稿存在同音字与断句错误，你可以依据上下文修正明显的错别字与专有名词，但不得改变原意。
3. 任何无法确定的字段，一律填"（文字稿未提及）"，不要留空，也不要臆造。
4. 参会人员姓名只有在文字稿中明确出现时才能填写；只听到"建设单位""承建单位"这类单位名称时，people 数组填空数组。
5. 区分"议定事项"与"讨论意见"：items 中只写会议明确形成结论、明确要求或明确安排的内容，不要罗列讨论过程。
6. 不要把推测写成事实。表述含糊时保留原话要点并标注"（表述不清，待核实）"。
7. **数字必须保留原样，不要补全单位。** 文字稿只说"八百"就写"八百"，不得自行写成"八百万元"或"800元"；确有需要说明时写成"八百（单位未明确，待核实）"。同理，日期、数量、比例、编号都不得推断补全。
8. 专业术语可以用规范说法替换口语表述（例如口语的"医院管理系统"可写为"HIS系统"），但不得改变原意，也不得引入文字稿中不存在的新概念、新主体或新事项。

【输出格式】
只输出一个 JSON 对象，不要输出任何解释、开场白或 Markdown 代码围栏。字段如下：

{
  "project": "项目名称；已知信息中提供了就照抄，否则从文字稿提取，都没有则填（文字稿未提及）",
  "meeting_no": "会议次数，格式如 第3次；无法确定填 第X次",
  "meeting_type": "必须从这五个里选一个：例会 / 专题会 / 启动会 / 评审会 / 其他",
  "topic": "会议主题，简明概括，20 字以内",
  "location": "会议地点",
  "time": "会议时间，格式如 2026年1月14日 上午",
  "host": "会议主持人姓名或身份",
  "recorder": "会议记录人",
  "publish_date": "纪要发布日期，通常与会议日期相同",
  "owner_unit": "建设单位全称",
  "builder_unit": "承建单位全称",
  "supervisor_unit": "监理单位全称",
  "signatories": {
    "建设单位": {"rep": "签收代表人姓名", "date": "签收日期"},
    "承建单位": {"rep": "签收代表人姓名", "date": "签收日期"},
    "监理单位": {"rep": "签收代表人姓名", "date": "签收日期"}
  },
  "intro": "会议引言段。参考句式：2026年1月14日上午，XX单位组织召开了XX项目第X次监理例会，会议主要围绕XX展开讨论。建设单位、承建单位及监理单位相关人员参加会议。用文字稿中的真实信息替换占位内容。",
  "items": [
    "第一条议定事项的完整内容（不要写序号，序号由程序添加）",
    "第二条议定事项的完整内容"
  ],
  "attendees": [
    {"unit": "建设单位", "people": ["姓名1", "姓名2"]},
    {"unit": "承建单位", "people": ["姓名1"]},
    {"unit": "监理单位", "people": ["姓名1"]}
  ]
}

【编写要求】
- items 是本次会议的核心产出，逐条写清楚"谁在什么时限内做什么"。文字稿中有明确时限的一并写入；只说"尽快""下周"这类模糊表述的，照实记录并标注（原文表述：尽快）。
- intro 用公文语体，一句话说清时间、组织单位、会议名称、讨论主题、参会各方。
  **若时间未提及，改用不含日期的句式**（例如"近日，XX单位组织召开了……"），
  不要把"（文字稿未提及）"塞进句子里 —— 那会让正式文件读起来很别扭。
- attendees 按建设单位、承建单位、监理单位顺序排列，只列实际出席的。
- 所有日期用中文书写，如 2026年1月14日。
- 输出必须是合法 JSON，字符串内的引号需转义。
- **自检**：输出前逐条检查 items，确认每一条都能在文字稿中找到出处；
  凡属你自行推断而非文字稿明确表述的内容，一律删除或改写为准确表述。
"""


TEMPLATES: dict[str, str] = {
    "supervision": SUPERVISION,
    "generic": GENERIC,
    "supervision_form": SUPERVISION_FORM,
}

# 需要输出 JSON 的模板
JSON_TEMPLATES = {"supervision_form"}


def build_form_user_prompt(transcript_text: str, known: dict | None = None) -> str:
    """构造表单填充用的用户提示词，把用户提供的已知信息一并交给模型。"""
    known = known or {}
    lines: list[str] = []

    facts = {
        "项目名称": known.get("project"),
        "建设单位全称": known.get("owner_unit"),
        "承建单位全称": known.get("builder_unit"),
        "监理单位全称": known.get("supervisor_unit"),
        "会议名称/主题": known.get("topic"),
        "会议地点": known.get("location"),
        "会议次数": known.get("meeting_no"),
    }
    provided = {k: v for k, v in facts.items() if v}

    if provided:
        lines.append("【已知信息】以下信息由用户提供，属准确事实，可直接采用，无需从文字稿推断：")
        for key, value in provided.items():
            lines.append(f"  - {key}：{value}")
        lines.append("")
    else:
        lines.append("【已知信息】用户未提供额外信息，请完全依据文字稿提取。")
        lines.append("")

    if known.get("notes"):
        lines.append(f"【补充说明】{known['notes']}")
        lines.append("")

    lines.append("【转写文字稿】")
    lines.append(transcript_text)
    lines.append("")
    lines.append("请据此按前述 JSON 结构输出会议纪要数据。")
    return "\n".join(lines)


def get_system_prompt(template: str | None = None) -> str:
    """解析模板名称或路径，返回系统提示词正文。"""
    key = (template or "supervision").strip()
    if key in TEMPLATES:
        return TEMPLATES[key]
    # 允许指向自定义提示词文件
    from pathlib import Path

    candidate = Path(key)
    if not candidate.is_absolute():
        from .config import PROJECT_ROOT

        candidate = PROJECT_ROOT / key
    if candidate.exists() and candidate.suffix.lower() in {".md", ".txt"}:
        return candidate.read_text(encoding="utf-8")
    raise KeyError(
        f"未找到纪要模板 '{key}'。内置模板：{'、'.join(TEMPLATES)}；"
        f"也可填写自定义提示词文件的相对路径。"
    )


def build_user_prompt(
    transcript_text: str,
    meeting_title: str = "",
    extra_notes: str = "",
) -> str:
    parts: list[str] = []
    if meeting_title:
        parts.append(f"本次会议名称（由用户提供）：{meeting_title}")
    if extra_notes:
        parts.append(f"用户补充说明：{extra_notes}")
    parts.append("以下是会议录音的转写文字稿，请据此整理会议纪要：")
    parts.append("")
    parts.append("<转写文字稿>")
    parts.append(transcript_text)
    parts.append("</转写文字稿>")
    return "\n".join(parts)


CHUNK_SYSTEM = """你是一名会议内容提炼助手。用户会给你一份会议转写文字稿的片段。

请提取该片段中所有具备归档价值的信息，包括：讨论议题、各方观点、达成的结论、分配的任务与责任人、时限要求、遗留问题、提到的具体数字与专有名词。

严格要求：
1. 只提取片段中实际出现的信息，不得推断或补充。
2. 保留原话中的关键表述与具体数字，不要概括掉细节。
3. 无法确定发言人身份时写"与会人员"。
4. 用简洁的条目式中文输出，不要写导语和总结。"""


CHUNK_USER = """这是会议转写文字稿的第 {index} / {total} 部分：

<片段>
{chunk}
</片段>"""


REDUCE_USER = """以下是从同一场会议不同片段中分别提炼出的要点，按时间顺序排列。

<分段提炼结果>
{merged}
</分段提炼结果>

请把这些要点整合为一份完整的会议纪要，并严格排除任何在这些要点中未出现的信息。"""


def split_for_chunks(text: str, max_chars: int, overlap_lines: int = 2) -> list[str]:
    """按行切分长文字稿，尽量不切断说话人段落。带少量重叠以保留上下文。"""
    if len(text) <= max_chars:
        return [text]

    lines = text.splitlines()
    chunks: list[str] = []
    current: list[str] = []
    size = 0

    for line in lines:
        line_len = len(line) + 1
        if size + line_len > max_chars and current:
            chunks.append("\n".join(current))
            tail = current[-overlap_lines:] if overlap_lines > 0 else []
            current = list(tail)
            size = sum(len(x) + 1 for x in current)
        current.append(line)
        size += line_len

    if current:
        chunks.append("\n".join(current))
    return chunks
