HYJY（会议纪要助手）

**HYJY（会议纪要助手）**是一款面向个人与工作场景的 AI 会议纪要工具。

它将录音、本地语音转文字、云端大模型总结、Word 文档生成整合为一套完整工作流：

🎙️ 录音 / 导入音频 → 📝 本地语音转写 → 🤖 AI 整理 → 📄 自动生成会议纪要

适用于项目例会、技术会议、需求讨论、现场沟通、工作汇报等场景。

✨ 核心特性
🎙️ 录音与音频导入
支持直接录音
支持导入已有录音文件
原始录音默认保存在本地
📝 本地语音转文字
支持 FunASR
可选 faster-whisper
音频首先在本地完成语音识别，减少原始录音上传风险
🤖 AI 自动整理会议纪要
支持通过云端大模型 API 进行内容整理
自动提取会议主题、讨论内容、决策事项、待办事项等
支持自定义 Prompt 和模型参数
📄 Word 会议纪要生成
根据预设 Word 模板自动生成会议纪要
保留既有办公文档格式
适合直接进入后续编辑、审核和归档流程
🖥️ 图形界面 + 命令行
提供桌面 GUI
同时支持 CLI 工作流
方便个人使用及自动化集成
⚙️ 灵活配置
支持 YAML 配置
API、模型、路径等参数与代码分离
支持不同使用环境快速切换
🛡️ 敏感信息保护
原始录音默认本地处理
提供敏感文件提交检查
建议根据实际数据敏感程度选择合适的模型和 API 服务


```
本机（音频全生命周期不出网）
  录音 WAV ──▶ 本地转写 ──▶ 文字稿
                              │
  ═══════════════ 合规边界 ══════════════
                              │  仅文字稿过线
  云端 LLM API ──▶ 结构化会议纪要 ──▶ 写回本机
```

数据处理原则

HYJY 将语音识别与大模型处理进行分离：

录音 → 本地 ASR → 文字稿 → 云端 LLM

因此，在默认工作流下，原始录音不需要直接发送至云端大模型。

## 为什么是这种架构

受限于本地设备硬件限制（显卡、内存容量不足），**无法运行可交付质量的本地大模型**。因此采用混合架构：

但需要注意：

会议文字稿本身仍可能包含个人信息、商业秘密、项目资料或其他敏感信息。

在处理公司、客户或其他敏感会议内容时，应根据所在单位的数据安全制度、项目要求以及所使用 AI 服务的隐私政策和服务条款进行判断。

快速开始
```bat
双击  安装.bat     REM 首次使用：创建运行环境、安装依赖、下载模型
双击  启动.bat     REM 日常使用：打开界面
```

## 一、安装

建议使用独立虚拟环境，避免污染系统 Python。

```bat
cd D:\workbuddy-test\meeting-minutes

python -m venv .venv
.venv\Scripts\activate

REM 关键：先把缓存与临时目录指向 D 盘，C 盘仅剩 8 GB
set PIP_CACHE_DIR=D:\workbuddy-test\meeting-minutes\.cache\pip
set TMP=D:\workbuddy-test\meeting-minutes\.cache\tmp

REM 核心依赖（很小）
pip install -r requirements.txt

REM 转写引擎：FunASR（当前选用，约需 3 GB 磁盘）
pip install -r requirements-funasr.txt

REM 录音模块（需录制系统音频时安装）
pip install -r requirements-record.txt
```

不需要录音功能可以跳过最后一步，用手机或会议软件自带录制导出音频后直接处理。

安装完成后建议先验证：

```bat
REM 离线自测：不加载模型、不调用云端，验证代码与环境
.venv\Scripts\python.exe tests\test_offline.py
```

23 项检查全部通过即可继续。转写或纪要报错时也可以先跑这个，用于区分是代码问题还是模型/网络问题。

## 二、配置 API Key

首次使用需要配置云端平台密钥。本项目使用 DeepSeek（合规强度最高、单价最低）。

**方式一：指向密钥文件（推荐）**

密钥本体不必放进项目目录，保持你原有的密钥集中管理习惯：

```yaml
# config.local.yaml
providers:
  deepseek:
    api_key_file: "D:/……/你的密钥文件.txt"
```

程序会读取该文件的第一行非空内容作为密钥，容忍 BOM 与多余空白。

**方式二：环境变量**

```bat
set DEEPSEEK_API_KEY=sk-你的密钥
```

**方式三：直接写入配置**（密钥会落在项目目录内，次选）

```bat
copy config.local.yaml.example config.local.yaml
notepad config.local.yaml
```

密钥解析优先级：**环境变量 > 密钥文件 > 内联配置**。`config.local.yaml` 已在
`.gitignore` 中，不会被提交。

配置好后运行自检：

```bat
python mm.py check --online
```

`--online` 会实际调用一次接口验证密钥与网络，建议加上。

## 三、使用

```bat
REM 环境自检
python mm.py check

REM 查看音频设备（排查录音问题时用）
python mm.py devices

REM 录音链路诊断：先验证麦克风与系统音频都能采到声音，再正式使用
.venv\Scripts\python.exe tests\test_recording.py --seconds 6 --play workspace\audio\某段音频.wav

REM 录制会议（Ctrl+C 结束；--auto-run 录完直接出纪要）
python mm.py record --name "监理例会" --auto-run

REM 处理已有音频，全流程
python mm.py run 会议录音.wav --title "监理例会" --notes "参会：建设单位、承建单位、监理单位"

REM 批量处理多个音频（模型只加载一次，强烈建议这样用）
python mm.py run 周一例会.wav 周三评审会.wav 周五协调会.wav

REM 出网内容预览：不调用云端，先看清将要发送什么（合规自查）
python mm.py run 会议录音.wav --dry-run

REM 只转写（先看文字稿质量，再决定是否生成纪要）
python mm.py run 会议录音.wav --skip-summary
python mm.py transcribe 会议录音.wav

REM 对已有文字稿重跑纪要（换模型或改提示词时用，无需重新转写）
python mm.py summarize workspace/transcript/xxx.transcript.md --title "监理例会"

REM 术语规范化：查看规则，预演改动效果（不改动文件）
python mm.py glossary
python mm.py glossary --test workspace\transcript\xxx.transcript.md
```

产物目录：

| 目录 | 内容 |
| --- | --- |
| `workspace/audio/` | 录音 WAV |
| `workspace/transcript/` | 转写文字稿（Markdown，带时间戳与说话人） |
| `workspace/minutes/` | 会议纪要（Markdown + Word 双份） |

### Word 版纪要

默认同时导出 `.docx`，使用原生 python-docx 实现，**不需要安装 pandoc**。

之所以不用 pandoc：它生成的是通用排版，无法控制中文字体，导出的公文既不是
仿宋也不是黑体，直接归档不合格。原生实现可以精确设置东亚字体。

默认排版（可在 `config.yaml` 的 `output.docx` 中调整）：

| 元素 | 默认值 |
| --- | --- |
| 标题 | 黑体 18pt 居中 |
| 各级标题 | 黑体 14pt 加粗 |
| 正文 | 仿宋 12pt，首行缩进两字符，固定行距 22pt |
| 表格 | 带边框，表头黑体加粗 |

若需严格对齐公文标准（三号仿宋_GB2312、行距 28pt），配置示例见
`config.local.yaml.example` 末尾。

### 按单位正式模板生成（推荐用于归档）

如果单位有固定的会议纪要模板（表格式公文），**不要用上面的通用排版**，
应当以模板为底稿填充 —— 模板里的字体、表格线、复选框、页边距都是单位标准，
重新排版会失去格式合规性。

```bat
python mm.py run 会议录音.wav ^
  --form "F:\项目用表（新）\新模板-标准化-20230802\监理文档模板\建设项目监理文档模板\02会议纪要.docx" ^
  --project "某息化建设项目" ^
  --owner-unit "某单位" ^
  --builder-unit "某公司" ^
  --supervisor-unit "某某" ^
  --meeting-no "第3次"
```

也可以在 `config.yaml` 的 `template` 段长期配置，避免每次输入长路径。

**为什么项目名称与单位全称要手动提供？** 因为这些是确定的事实，模型无法从
录音里可靠推断。让它猜一个单位全称，写进正式归档文件的错误代价很高。提供后
模型只需专注于从文字稿中提取会议内容。

产物三份，同目录：

| 文件 | 用途 |
| --- | --- |
| `<名称>.minutes.docx` | 填充后的正式模板文件，可直接提交归档 |
| `<名称>.minutes.md` | 可读的文字版，供人工复核 |
| `<名称>.minutes.json` | 结构化数据，便于追溯模型究竟填了什么 |

实现细节：

- **模板按标签驱动填充**，不硬编码行列坐标，模板微调后仍可用
- **复选框**是 Wingdings 符号，勾选只是把字符 `00A8`（空框）改为 `00FE`（打勾框），
  与原模板完全一致，而不是替换成 Unicode 字符
- **条目多于模板预留槽位时自动增行**，继承模板原有段落格式
- **模板原文件永不被改写**，每次都是在副本上操作

> 注意：`doc_to_image`（把文档渲染成图片）在含全角括号的路径下会报文件不存在，
> 但 python-docx 读取与填充不受影响，本工具不依赖该功能。

## 四、切换云端平台

改 `config.yaml` 的 `llm.provider`，或运行时加参数：

```bat
python mm.py providers                    REM 查看全部平台与合规说明
python mm.py run 录音.wav --provider aliyun_bailian
```

新增平台只需在 `config.yaml` 的 `providers` 段追加一段配置，**无需改动任何代码**：

```yaml
providers:
  intranet:
    label: "单位内网大模型"
    base_url: "http://10.0.0.5:8000/v1"
    model: "your-model-name"
    api_key_env: "INTRANET_API_KEY"
```

所有平台统一走 OpenAI 兼容协议，因此兼容任何符合该协议的服务。

## 五、切换转写引擎

改 `config.yaml` 的 `transcription.backend`，或运行时加参数：

```bat
python mm.py backends
python mm.py run 录音.wav --backend faster_whisper
```

| 引擎 | 中文准确率 | 说话人分离 | 模型体积 | 实测耗时 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `funasr` | **更优** | 内置 cam++ | 约 1 GB | 80~103 秒 | 当前选用，中文场景首选 |
| `faster_whisper` | 一般 | 无 | small 约 461 MB | 约 28 秒 | 备选，多语种支持更好 |

两个引擎共用同一套输出格式与后续流程，切换后文字稿与纪要格式完全一致。

### 实测对比（同一段中文音频）

| 引擎 | 转写结果 | 判定 |
| --- | --- | --- |
| `funasr` | 欢迎大家来体验**达摩院**推出的语音识别模型**。** | 专有名词正确，带标点，带说话人 |
| `faster_whisper` small | 欢迎大家来体验**打磨院**推出的语音识别模型 | 专有名词错误，无标点，无说话人 |

证据表明单就中文专有名词而言，FunASR 明显更可靠。若切换到 faster_whisper，
建议至少使用 `medium` 以上规格，或配置 `initial_prompt` 引导词。

切换引擎重跑时，旧文字稿会按引擎名自动归档（如 `xxx.faster_whisper.transcript.md`）
而不是被覆盖，方便对比与回溯。

### faster_whisper 的镜像要求（重要）

**实测本机直连 `huggingface.co` 不通（HTTP 000），必须走镜像。** 已在
`config.yaml` 中默认配置 `hf_endpoint: "https://hf-mirror.com"`（实测可用）。

另外，程序刻意绕开了 huggingface_hub 的缓存机制，改为逐文件直接下载到
`models/faster-whisper/<规格>/`。原因是：Windows 未开启开发者模式时 hub 无法
创建符号链接，会在快照目录留下 0 字节占位文件，加载时报
`File model.bin is incomplete`。当前实现同时确保模型落在 D 盘而非 C 盘。

### 提高识别准确率

专有名词识别不准时，有两个可用的手段，作用层次不同。

#### 手段一：热词（提升 ASR 本身的识别率）

已预置一批信息化监理领域热词，可直接使用或按项目增补：

```yaml
transcription:
  funasr:
    hotword: "某某科技,信息化工程监理,总监理工程师,监理通知单,初步验收,最终验收,竣工验收,隐蔽工程,综合布线,桥架,弱电井,核心交换机,业务系统,建设单位,承建单位,监理单位"
```

可用逗号、顿号或空格分隔，程序会自动转换引擎要求的格式并去重。
**热词过多反而会分散模型注意力，建议控制在 30 个以内。**

#### 手段二：术语规范化（转写后确定性替换）

热词只能影响 ASR 的识别过程，结果不可预期；术语表在转写完成后做确定性替换，
结果可审计、可随时调整，**改完术语表对已有文字稿重跑即可，无需重新转写**。

**默认关闭，这是有意的。** 盲目替换会破坏正常语句 —— 例如把"建立"改成"监理"，
会让"建立制度""建立台账"这类合法表述出错。请依据你在真实录音里实际观察到的
误识别再配置。

```yaml
transcription:
  glossary:
    enabled: true
    replacements:          # 同音误识别纠正
      "俊工": "竣工"
      "峻工": "竣工"
      "招镖": "招标"
    regex:                 # 正则替换，处理变体写法
      - pattern: "监理(工程师)?通知单"
        replace: "监理通知单"
```

**启用前务必先预演**，查看规则实际会改动哪些内容：

```bat
python mm.py glossary                          REM 查看当前规则
python mm.py glossary --test workspace\transcript\某文字稿.md   REM 预演，不改动文件
```

预演会逐条显示"改前 / 改后"的上下文对照，确认没有改坏正常语句后再启用。

规则按**长度倒序**应用，因此同时配置"监理单位"和"监理"时，长词会优先匹配，
不会被短词截断。替换只改文本，不影响时间戳与说话人，与录音的对照关系不受影响。

## 六、硬件约束与实测数据（重要）


### 实测耗时（本机）

| 场景 | 耗时 | 说明 |
| --- | --- | --- |
| 首次转写（含模型下载） | 约 2 分 53 秒 | 下载 paraformer-large、ct-punc、cam++、fsmn-vad 合计约 2.1 GB |
| 批量第 1 个文件 | 约 103 秒 | 其中绝大部分是**模型加载的固定开销** |
| 批量第 2 个文件起 | **约 2 秒** | 模型复用后，每个文件只付推理时间 |
| **真实会议 47 分 47 秒** | **17 分 06 秒** | **约 2.8 倍速**，输出 1218 段、识别 6 个说话人 |
| 云端生成纪要 | 约 20 秒 | 输入约 1.5 万 token，输出约 800 token |

**必须知道的一点**：每次运行都有一笔约 75~100 秒的模型加载固定开销，与音频长短无关。
因此**处理多个音频时务必一次传入，让模型只加载一次** —— 实测第 2 个文件仅需 2 秒，
比逐个运行快约 50 倍。

```bat
REM 好：模型只加载一次
python mm.py run 会议1.wav 会议2.wav 会议3.wav

REM 差：每个文件都重新加载模型，白等 3 分钟
python mm.py run 会议1.wav
python mm.py run 会议2.wav
python mm.py run 会议3.wav
```

`transcription.funasr.batch_size_s` 默认为 300 秒。实测 47 分钟音频时进程内存占用
达 **2.28 GB**，而本机空闲约 2.5 GB，余量过小。**建议调小到 100~150** 以留出安全边距。

### 真实会议的质量边界（重要）

拿一场真实会议（单声道会议室录音，多人同时说话、离麦较远）与人工撰写的纪要逐项对照：

| 信息类型 | 自动提取效果 |
| --- | --- |
| 议定事项、需求内容、方案讨论 | **良好**，能抓住实质，条目具体 |
| 金额、数量、比例 | **可用**，但单位可能缺失（原文只说"八百"就不会补成"万元"） |
| 项目名称、机构全称 | **不可靠**，需人工提供或用 `--project` 等参数补 |
| **参会人员姓名** | **基本丢失**，嘈杂录音中专有名词几乎必然识别错误 |
| 时间、地点、主持人 | 通常丢失（录音里往往没人说） |

**结论：把本工具定位为"起草助手"而非"全自动"。** 会议内容部分能省掉大部分
整理工作，但参会人员、机构全称、金额单位这类字段必须人工补齐 —— 这也正是
`--project` / `--owner-unit` 等参数存在的意义。

补齐时**不必重新调用云端**：改正 `<名称>.minutes.json` 后运行

```bat
python mm.py fill workspace\minutes\xxx.minutes.json
```

即可重新填充模板，不再产生 API 费用，也不会把已改好的内容覆盖回去。

### 录音链路的已知特性

**WASAPI loopback 只在系统正在播放声音时才产出数据。** 实测确认：静默状态下
loopback 的 `get_read_available()` 恒为 0，此时若用阻塞式读取会永久挂住。
程序已改用非阻塞轮询，并对没有数据的一路补静音，保证时间轴长度准确。

因此诊断录音时若未播放任何声音，"系统音频"一路显示为静音属正常现象，
请用 `tests\test_recording.py --play <音频文件>` 复测。

实测结果（播放测试音频时的真实数据）：

```
系统音频：扬声器 (Realtek(R) Audio) [Loopback]  48000 Hz / 2ch
   采集到 79840 采样点（预期 80000，达成率 100%），音量 -12.5 dBFS
麦克风  ：麦克风 (Realtek(R) Audio)             44100 Hz / 2ch
   采集到 79608 采样点（预期 80000，达成率 100%），音量 -56.9 dBFS
```

两路混音采用等权平均：同时有声音时不会削波，代价是各自衰减约 6 dB，
该衰减对语音识别无影响。

### C 盘零写入（已实测确认）

程序会强制把以下几类缓存重定向到 D 盘，避免占用系统盘：

| 缓存 | 环境变量 | 落点 |
| --- | --- | --- |
| modelscope / FunASR 模型 | `MODELSCOPE_CACHE` | `models/` |
| HuggingFace / torch | `HF_HOME`、`TORCH_HOME` | `.cache/` |
| pip 缓存 | `PIP_CACHE_DIR` | `.cache/pip/` |
| 系统临时目录 | `TMPDIR`、`TEMP`、`tempfile.tempdir` | `.cache/tmp/` |

其中临时目录需要特别处理：Python 的 `tempfile` 会缓存首次解析结果，之后改环境
变量就不再生效。FunASR 依赖的 jieba 会往临时目录写约 9 MB 词表缓存，若不强制
覆盖 `tempfile.tempdir`，它会落到 C 盘用户 Temp 目录。程序已显式覆盖该值。

实测本项目对 C 盘的占用为 **44 KB**（仅 huggingface 的一个小缓存目录）。

## 七、故障排查

| 现象 | 原因与处理 |
| --- | --- |
| `401 认证失败` | 密钥无效或未生效，用 `mm.py check` 查看密钥来源 |
| `404 接口地址或模型名不存在` | 核对 `providers.<名称>` 的 `base_url` 与 `model` |
| `SSL 校验失败` | 内网或代理环境，设置 `llm.verify_ssl: false` |
| 调用超时 | 文字稿过长，调大 `llm.timeout` 或调小 `llm.chunk_threshold` 启用分段摘要 |
| 转写时系统卡顿 | 内存不足，调小 `batch_size_s`，或改用 faster_whisper 的 small 模型 |
| `502 Bad Gateway` 或模型下载失败 | HuggingFace 直连不通。检查 `transcription.faster_whisper.hf_endpoint` 是否为可用镜像 |
| `File model.bin is incomplete` | Windows 符号链接权限问题导致的残缺缓存。删除 `models/faster-whisper/models--*` 后重跑即可 |
| `unrecognized arguments: --backend` | 已修复。若仍遇到，确认 `mm.py` 为最新版本，通用参数需挂在子命令上 |
| 录音无声 | 检查系统输出设备是否启用；用 `python mm.py devices` 确认存在带 loopback 标记的设备 |
| 系统音频一路全静音 | WASAPI loopback 只在系统有声音播放时产出数据。确认会议软件的声音确实从扬声器输出，而非蓝牙耳机等其他设备 |
| 录音文件时长异常 | 正常。每个分片都会补齐到精确的 `recording.chunk_seconds` 长度，以保持时间轴准确 |
| 中文乱码 | 确保终端编码为 UTF-8；程序内部已强制 UTF-8 输出 |

## 八、合规提醒

1. **必须使用开放平台 API，不要使用网页版或 App。** 同一厂商两条轨道的数据条款完全不同，网页端通常默认参与模型优化。
2. **"不训练"不等于"零留存"。** 国内平台均不承诺零留存，日志留存属法定义务。
3. **涉密会议的文字稿不要送入任何公网大模型。** 建议仅用于内部协调会、技术评审会等一般性会议。
4. **录音须取得参会人知情同意**，会前明示告知。
5. **纪要为机器生成，归档前必须人工复核。** 提示词已加入强反编造约束，但仍可能出现事实性错误。

### 发送前自查：出网内容预览

会议文字稿属于内部信息，出网前应当能看清具体送出了什么。加 `--dry-run` 即可：

```bat
python mm.py run 会议录音.wav --dry-run
```

它**不会调用任何云端接口**，只把将要发送的完整内容写入
`workspace/minutes/<名称>.prompt.md`，包含：目标平台、接口地址、模型名称、
系统提示词全文、以及实际携带的文字稿全文。审阅确认后再去掉 `--dry-run` 重跑。

这个模式**不需要配置 API Key** 即可使用，因此可以在申请密钥之前先确认工具行为是否符合要求。

### 版本库安全：自动拦截敏感文件

本项目的仓库里同时存在源码和业务数据 —— `workspace/` 下是真实的会议录音与纪要
（含单位名称、项目名称、人员姓名），`config.local.yaml` 里是密钥文件路径。
这两类东西一旦提交并推送，**即使随后删除，历史里仍然留有记录**。

靠人每次提交前手动核对是靠不住的，因此提供自动检查：

```bat
python scripts\check_staged.py --install   REM 安装为 pre-commit 钩子（安装.bat 已自动执行）
python scripts\check_staged.py             REM 手动检查暂存区
```

安装后每次 `git commit` 都会先检查，发现违规直接阻止提交并列出问题文件：

```
================================================================
  提交已被阻止 —— 暂存区中包含不应入库的文件
================================================================

  workspace/audio/某录音.wav  ← 命中禁止目录 workspace/
  scripts/_probe.py           ← 疑似硬编码密钥（第 1 行）：sk-abcdefghi****

  这些文件可能包含会议录音、单位与项目信息，或密钥路径。
  一旦推送即无法撤回，请务必排除后再提交。
================================================================
```

检查包含两层：**路径规则**（禁止目录、文件类型）与**内容扫描**
（识别形如 `sk-xxxxxxxxxxxxxxxxxxxx` 的真实密钥）。

> 注意：`.git/hooks` 不进版本库，换一台机器克隆下来钩子不会自动出现，
> 因此每个新环境都需要执行一次 `--install`（`安装.bat` 已包含这一步）。

## 九、项目结构

```
meeting-minutes/
├── 安装.bat / 启动.bat        图形界面的安装与启动
├── app/                       图形界面
│   ├── main.py                入口
│   ├── main_window.py         主窗口（左导航 + 六页 + 步骤衔接）
│   ├── worker.py              后台线程（转写 / 纪要 / 自检）
│   ├── theme.py               样式表
│   ├── utils.py               打开文件等小工具
│   ├── widgets/               公用组件
│   │   ├── file_queue.py      文件队列（拖拽 + 清单 + 状态）
│   │   └── step_page.py       分步页面骨架（进度 + 日志 + 取消）
│   └── pages/
│       ├── record_page.py     ① 录制
│       ├── transcribe_page.py ② 转写
│       ├── minutes_page.py    ③ 纪要
│       ├── library_page.py    文件库
│       ├── settings_page.py   设置
│       └── help_page.py       帮助
├── mm.py                      CLI 入口
├── mmtools/                   业务层（界面与命令行共用）
│   ├── config.py              配置加载、分层合并、密钥解析
│   ├── transcribers.py        可插拔转写后端（FunASR / faster-whisper）
│   ├── summarizer.py          可配置云端大模型客户端
│   ├── prompts.py             纪要提示词模板
│   ├── glossary.py            术语规范化
│   ├── exporter.py            Word 导出（通用排版）
│   ├── docx_template.py       按单位模板填充
│   ├── recorder.py            WASAPI loopback + 麦克风录音
│   ├── progress.py            进度回报与取消信号
│   └── pipeline.py            端到端编排
├── scripts/
│   └── check_staged.py        提交前敏感文件检查
├── config.yaml                主配置（可提交）
├── config.local.yaml          本地覆盖与密钥（不提交）
├── docs/                      方案文档与界面截图
├── workspace/                 产物：录音、文字稿、纪要（不提交）
├── models/                    模型缓存（D 盘，不提交）
├── .cache/                    运行缓存（D 盘，不提交）
└── tests/
    ├── test_offline.py        离线自测 35 项
    ├── test_form_mode.py      模板填充 13 项
    ├── test_llm_mock.py       云端链路 12 项（本地模拟服务）
    ├── test_gui_smoke.py      界面冒烟 11 项
    └── test_recording.py      录音链路诊断
```
## 十、隐私与数据安全

HYJY 的设计目标之一是尽可能减少原始音频数据的外部传输。

默认工作流：

```
原始录音
   │
   ├── 本地保存
   │
   └── 本地 ASR
          ↓
        文字稿
          ↓
      云端大模型
          ↓
       会议纪要
```

但需要特别注意：

1. 文字稿也可能属于敏感数据

如果会议涉及：

客户信息
商业秘密
项目内部资料
个人信息
政府/政务信息
涉密信息

请根据实际要求决定是否允许使用云端大模型。

2. 涉密信息禁止直接提交公网 AI 服务

对于涉密或明确禁止外传的数据，应使用符合单位安全要求的本地模型或内部 AI 服务。

3. 正式归档前人工审核

AI 生成的会议纪要仅作为辅助结果。

正式提交、发送或归档前，应由工作人员进行审核。

## 十一、 Git 安全

项目包含提交前敏感文件检查机制，用于降低以下内容被意外提交到 Git 仓库的风险：

API Key
密码
本地配置
音频文件
会议文字稿
生成的会议纪要
其他敏感文件

但自动检查不能保证发现所有敏感信息。

提交代码前仍应人工检查 Git diff。

建议：

git status
git diff --cached

确认没有敏感信息后再执行：

git commit
git push


## 十二、 使用场景

HYJY 适用于需要快速整理会议内容的工作场景，例如：

项目例会
技术讨论
需求分析
工作汇报
项目协调
现场沟通
软件开发会议
信息化项目会议
个人学习与访谈记录

## 十三、当前状态

HYJY 是一个持续开发中的个人项目。

当前主要功能已经可以完成：

录音 / 音频文件
        ↓
本地语音识别
        ↓
文字稿
        ↓
AI 会议内容整理
        ↓
Word 会议纪要

后续计划包括：

更完善的实时录音体验

更准确的说话人识别

会议内容分段与时间轴

更丰富的会议纪要模板

本地大模型支持

更完善的 GUI

批量处理

更多 ASR / LLM 后端

打包为 Windows 独立应用

## 十四、后续可扩展方向

- 增加本地说话人分离（FunASR 的 cam++ 已内置，若准确率不足可叠加 pyannote）
- 接入单位知识库，把历史纪要作为参考上下文
- 配置 `output.export_docx: true` 并安装 pandoc，可直接导出 Word 版纪要
- 通过 Windows 任务计划做定时批处理（例如每天定时处理当日录音）

## 十五、 开源与贡献

欢迎提出 Issue、提交 Pull Request 或分享改进建议。

如果你基于 HYJY 进行了修改或二次开发，请遵守本项目 License 以及项目所使用的第三方依赖各自的许可证要求。

## 十六、 License

本项目采用 MIT License。

Copyright (c) 2026 xiyiHan

本项目所使用的第三方软件、模型、SDK 和服务分别遵循其各自的许可证及服务条款。

## 十七、 免责声明

HYJY 是一个用于提高会议记录和整理效率的软件工具。

项目作者不对 AI 生成内容的准确性、完整性或适用于特定业务场景作任何保证。

对于正式文件、合同、项目资料、客户资料以及其他需要承担责任的内容：

请务必进行人工审核。

使用本软件处理数据时，请遵守所在单位的数据安全制度、保密要求以及适用的法律法规。


