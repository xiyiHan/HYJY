"""帮助页：环境自检与常见问题。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from mmtools.config import Config

FAQ_HTML = """
<style>
  p { margin: 6px 0 12px 0; line-height: 1.6; }
  .q { font-weight: 500; margin-top: 16px; }
  .a { margin-left: 0; color: #333; }
</style>

<p class="q">密钥无效或连接失败</p>
<p class="a">到「设置」页检查密钥文件路径，然后点「测试连接」。
注意必须使用开放平台 API，不要用网页版的密钥；两者不是一回事。</p>

<p class="q">模型下载失败</p>
<p class="a">FunASR 的模型来自 ModelScope，faster-whisper 的模型来自 HuggingFace。
国内直连 HuggingFace 通常不通，程序已默认配置 hf-mirror.com 镜像。
若仍失败，检查网络或在设置中确认引擎选择。</p>

<p class="q">转写中途失败或电脑卡顿</p>
<p class="a">转写是内存密集型操作，实测一场 47 分钟会议的峰值内存约 2.3 GB。
处理前请关闭浏览器等占用内存的程序。若仍不足，在 config.yaml 中把
transcription.funasr.batch_size_s 从 150 继续调小到 80 或 60。</p>

<p class="q">为什么一次要等这么久</p>
<p class="a">每次启动都有约 100 秒的模型加载固定开销，与音频长短无关。
之后按实测约 2.8 倍速处理。所以一次拖入多个文件比逐个处理快得多 ——
模型只加载一次。</p>

<p class="q">纪要里出现「（文字稿未提及）」</p>
<p class="a">这是有意为之：程序不会编造信息。要减少这类占位，
在「设置」页把项目名称、建设单位、承建单位、监理单位全称填上。</p>

<p class="q">参会人员姓名识别不出来</p>
<p class="a">这是当前方案的已知边界。嘈杂会议室录音里，人名这类专有名词
几乎必然识别错误。建议生成后手工补齐，改完在设置页更新即可。</p>

<p class="q">取消按钮为什么不是立刻停止</p>
<p class="a">转写是一个不可中断的计算过程，取消只能在文件之间或阶段之间生效。
已经转写完的文字稿会保留，不会白费。</p>

<p class="q">金额的单位不见了</p>
<p class="a">如果录音里只说「八百」而没提单位，程序会照实写成
「八百（单位未明确，待核实）」，而不是替你补成「八百万元」。
归档文件里的金额不能靠推断，请人工确认。</p>
"""


class HelpPage(QWidget):
    """帮助页。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cfg = Config.load()
        self._worker = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        tabs = QTabWidget()
        root.addWidget(tabs, 1)

        # ---- 自检 ----
        check_tab = QWidget()
        v = QVBoxLayout(check_tab)
        v.setContentsMargins(12, 12, 12, 12)

        row = QHBoxLayout()
        self.btn_check = QPushButton("运行环境自检")
        self.btn_check.setObjectName("primary")
        self.btn_check.clicked.connect(lambda: self.run_check(online=False))
        self.btn_check_online = QPushButton("自检并测试云端连接")
        self.btn_check_online.clicked.connect(lambda: self.run_check(online=True))
        row.addWidget(self.btn_check)
        row.addWidget(self.btn_check_online)
        row.addStretch(1)
        v.addLayout(row)

        self.check_output = QPlainTextEdit()
        self.check_output.setReadOnly(True)
        self.check_output.setObjectName("logView")
        self.check_output.setPlaceholderText("点上面的按钮开始自检")
        v.addWidget(self.check_output, 1)
        tabs.addTab(check_tab, "环境自检")

        # ---- 常见问题 ----
        faq = QTextBrowser()
        faq.setOpenExternalLinks(True)
        faq.setHtml(FAQ_HTML)
        faq.setFrameShape(QTextBrowser.Shape.NoFrame)
        tabs.addTab(faq, "常见问题")

        # ---- 关于 ----
        about = QLabel(
            "会议纪要工具\n\n"
            "本地转写 + 云端生成纪要。音频全程不出本机，只有文字稿会发送到云端。\n\n"
            "使用前请确认已获得参会人对录音的知情同意。\n"
            "涉密会议的文字稿不要送入任何公网大模型。\n\n"
            "纪要为机器生成，归档前必须人工复核。"
        )
        about.setWordWrap(True)
        about.setAlignment(Qt.AlignmentFlag.AlignTop)
        about.setContentsMargins(16, 16, 16, 16)
        tabs.addTab(about, "关于")

    # ---------------------------------------------------------------- 自检

    def run_check(self, online: bool = False) -> None:
        from app.worker import CheckWorker

        self.btn_check.setEnabled(False)
        self.btn_check_online.setEnabled(False)
        self.check_output.setPlainText("正在检测……")
        self._worker = CheckWorker(self.cfg, online=online, parent=self)
        self._worker.finished_ok.connect(self._on_check_ok)
        self._worker.failed.connect(self._on_check_failed)
        self._worker.finished.connect(self._on_check_finished)
        self._worker.start()

    def _on_check_ok(self, text: str) -> None:
        self.check_output.setPlainText(text)

    def _on_check_failed(self, message: str) -> None:
        self.check_output.setPlainText(f"自检失败：{message}")

    def _on_check_finished(self) -> None:
        self.btn_check.setEnabled(True)
        self.btn_check_online.setEnabled(True)
        self._worker = None
