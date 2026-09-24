"""设置页：把最常改的配置项做成可视化表单。

仍然写回 config.local.yaml，与命令行版共用同一份配置 —— 两种使用方式
不冲突，用户也可以随时手改文件。
"""

from __future__ import annotations

from pathlib import Path

import yaml
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mmtools.config import LOCAL_CONFIG_FILE, PROJECT_ROOT, Config


class SettingsPage(QWidget):
    """配置编辑页。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cfg = Config.load()
        self._build_ui()
        self.load_values()

    # ---------------------------------------------------------------- 界面

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)

        container = QWidget()
        scroll.setWidget(container)
        root = QVBoxLayout(container)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(6)

        # ---------- 云端平台 ----------
        box_llm = QGroupBox("云端平台（用于生成会议纪要）")
        form_llm = QFormLayout(box_llm)

        self.cmb_provider = QComboBox()
        self.lbl_compliance = QLabel("")
        self.lbl_compliance.setObjectName("hint")
        self.lbl_compliance.setWordWrap(True)

        self.ed_key_file = QLineEdit()
        btn_key = QPushButton("浏览")
        btn_key.clicked.connect(self._pick_key_file)
        row_key = QHBoxLayout()
        row_key.addWidget(self.ed_key_file, 1)
        row_key.addWidget(btn_key)
        holder_key = QWidget()
        holder_key.setLayout(row_key)

        self.ed_model = QLineEdit()
        self.lbl_key_status = QLabel("")
        self.lbl_key_status.setObjectName("hint")

        self.btn_test = QPushButton("测试连接")
        self.btn_test.clicked.connect(self._test_connection)

        form_llm.addRow("平台", self.cmb_provider)
        form_llm.addRow("合规说明", self.lbl_compliance)
        form_llm.addRow("密钥文件", holder_key)
        form_llm.addRow("密钥状态", self.lbl_key_status)
        form_llm.addRow("模型名称", self.ed_model)
        form_llm.addRow("", self.btn_test)
        root.addWidget(box_llm)

        self.cmb_provider.currentTextChanged.connect(self._on_provider_changed)

        # ---------- 转写引擎 ----------
        box_asr = QGroupBox("转写引擎（本地运行，音频不出本机）")
        form_asr = QFormLayout(box_asr)

        self.cmb_backend = QComboBox()
        self.cmb_backend.addItems(["funasr", "faster_whisper"])

        self.ed_hotword = QLineEdit()
        self.ed_hotword.setPlaceholderText("用逗号分隔，例如：某单位,冷链监控,业务使用")
        self.lbl_hotword_hint = QLabel(
            "热词要按会议主题定制。主题不匹配的热词等于没填 —— 这是实测教训。"
        )
        self.lbl_hotword_hint.setObjectName("hint")
        self.lbl_hotword_hint.setWordWrap(True)

        form_asr.addRow("引擎", self.cmb_backend)
        form_asr.addRow("热词", self.ed_hotword)
        form_asr.addRow("", self.lbl_hotword_hint)
        root.addWidget(box_asr)

        # ---------- 会议纪要模板 ----------
        box_tpl = QGroupBox("会议纪要模板与已知信息")
        form_tpl = QFormLayout(box_tpl)

        self.ed_template = QLineEdit()
        self.ed_template.setPlaceholderText("留空则输出通用排版的 Word 纪要")
        btn_tpl = QPushButton("浏览")
        btn_tpl.clicked.connect(self._pick_template)
        row_tpl = QHBoxLayout()
        row_tpl.addWidget(self.ed_template, 1)
        row_tpl.addWidget(btn_tpl)
        holder_tpl = QWidget()
        holder_tpl.setLayout(row_tpl)

        self.ed_project = QLineEdit()
        self.ed_owner = QLineEdit()
        self.ed_builder = QLineEdit()
        self.ed_supervisor = QLineEdit()

        hint_tpl = QLabel(
            "下面四项是确定的事实，填在这里可以避免模型去猜。"
            "模型猜错的单位全称会直接写进归档文件，代价很高。"
        )
        hint_tpl.setObjectName("hint")
        hint_tpl.setWordWrap(True)

        form_tpl.addRow("模板文件", holder_tpl)
        form_tpl.addRow("项目名称", self.ed_project)
        form_tpl.addRow("建设单位全称", self.ed_owner)
        form_tpl.addRow("承建单位全称", self.ed_builder)
        form_tpl.addRow("监理单位全称", self.ed_supervisor)
        form_tpl.addRow("", hint_tpl)
        root.addWidget(box_tpl)

        # ---------- 操作 ----------
        actions = QHBoxLayout()
        self.btn_save = QPushButton("保存设置")
        self.btn_save.setObjectName("primary")
        self.btn_save.clicked.connect(self.save_values)
        self.btn_reload = QPushButton("放弃修改")
        self.btn_reload.clicked.connect(self.load_values)
        actions.addWidget(self.btn_save)
        actions.addWidget(self.btn_reload)
        actions.addStretch(1)
        self.lbl_save_state = QLabel("")
        self.lbl_save_state.setObjectName("hint")
        actions.addWidget(self.lbl_save_state)
        root.addLayout(actions)

        cfg_path = PROJECT_ROOT / LOCAL_CONFIG_FILE
        tip = QLabel(
            f"配置写入：{cfg_path}\n"
            f"该文件含有密钥路径，已加入 .gitignore，不会提交到版本库。"
        )
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        root.addWidget(tip)
        root.addStretch(1)

    # ---------------------------------------------------------------- 读写

    def load_values(self) -> None:
        cfg = self.cfg

        providers = list(cfg.providers().keys())
        self.cmb_provider.blockSignals(True)
        self.cmb_provider.clear()
        self.cmb_provider.addItems(providers)
        current = cfg.get("llm", "provider") or "deepseek"
        if current in providers:
            self.cmb_provider.setCurrentText(current)
        self.cmb_provider.blockSignals(False)
        self._on_provider_changed(self.cmb_provider.currentText())

        _, prov = cfg.provider()
        self.ed_model.setText(prov.get("model") or "")

        # 密钥文件路径存在 config.local.yaml 里
        local = self._read_local()
        key_file = ((local.get("providers") or {}).get(self.cmb_provider.currentText()) or {}).get("api_key_file", "")
        self.ed_key_file.setText(str(key_file or ""))
        self._refresh_key_status()

        self.cmb_backend.setCurrentText(cfg.backend_name)
        self.ed_hotword.setText(
            str(cfg.get("transcription", "funasr", "hotword") or "")
        )

        tpl_conf = cfg.get("template") or {}
        self.ed_template.setText(str(tpl_conf.get("path") or ""))
        known = tpl_conf.get("known") or {}
        self.ed_project.setText(str(known.get("project") or ""))
        self.ed_owner.setText(str(known.get("owner_unit") or ""))
        self.ed_builder.setText(str(known.get("builder_unit") or ""))
        self.ed_supervisor.setText(str(known.get("supervisor_unit") or ""))

        self.lbl_save_state.setText("")

    def _read_local(self) -> dict:
        path = PROJECT_ROOT / LOCAL_CONFIG_FILE
        if not path.exists():
            return {}
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}

    def save_values(self) -> None:
        local = self._read_local()

        provider = self.cmb_provider.currentText().strip()
        local.setdefault("llm", {})["provider"] = provider

        prov_local = local.setdefault("providers", {}).setdefault(provider, {})
        key_file = self.ed_key_file.text().strip()
        if key_file:
            prov_local["api_key_file"] = key_file
        model = self.ed_model.text().strip()
        if model:
            prov_local["model"] = model

        local.setdefault("transcription", {})["backend"] = self.cmb_backend.currentText().strip()
        hotword = self.ed_hotword.text().strip()
        local["transcription"].setdefault("funasr", {})["hotword"] = hotword

        tpl_local = local.setdefault("template", {})
        template = self.ed_template.text().strip()
        tpl_local["path"] = template
        tpl_local["enabled"] = bool(template)
        tpl_local["known"] = {
            "project": self.ed_project.text().strip(),
            "owner_unit": self.ed_owner.text().strip(),
            "builder_unit": self.ed_builder.text().strip(),
            "supervisor_unit": self.ed_supervisor.text().strip(),
        }

        path = PROJECT_ROOT / LOCAL_CONFIG_FILE
        try:
            header = (
                "# 本地配置覆盖（已在 .gitignore 中，不会被提交）\n"
                "# 本文件由图形界面写入；也可直接手工编辑。\n\n"
            )
            body = yaml.safe_dump(local, allow_unicode=True, sort_keys=False, default_flow_style=False)
            path.write_text(header + body, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", f"写入配置失败：{exc}")
            return

        # 重新加载，让命令行与界面共用同一份最新配置
        try:
            self.cfg = Config.load()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "配置已写入但重新加载失败", str(exc))
        self.lbl_save_state.setText("已保存")
        self._refresh_key_status()

    # ---------------------------------------------------------------- 交互

    def _on_provider_changed(self, name: str) -> None:
        try:
            _, prov = self.cfg.provider(name)
        except Exception:
            return
        self.lbl_compliance.setText(
            f"{prov.get('compliance', '（未注明）')}\n"
            f"接口：{prov.get('base_url')}"
        )
        self.ed_model.setText(prov.get("model") or "")

    def _refresh_key_status(self) -> None:
        try:
            source = self.cfg.api_key_source(self.cmb_provider.currentText())
            token = self.cfg.api_key(self.cmb_provider.currentText())
            if token:
                self.lbl_key_status.setText(f"{source}（{self.cfg.mask_secret(token)}）")
                self.lbl_key_status.setObjectName("ok")
            else:
                self.lbl_key_status.setText("未配置，无法生成纪要")
                self.lbl_key_status.setObjectName("warn")
        except Exception as exc:  # noqa: BLE001
            self.lbl_key_status.setText(str(exc))
            self.lbl_key_status.setObjectName("warn")
        self.lbl_key_status.style().unpolish(self.lbl_key_status)
        self.lbl_key_status.style().polish(self.lbl_key_status)

    def _pick_key_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择存放 API Key 的文件", "", "文本文件 (*.txt);;所有文件 (*)"
        )
        if path:
            self.ed_key_file.setText(path)

    def _pick_template(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择会议纪要模板", "", "Word 模板 (*.docx);;所有文件 (*)"
        )
        if path:
            self.ed_template.setText(path)

    def _test_connection(self) -> None:
        from mmtools.summarizer import CloudLLM

        self.btn_test.setEnabled(False)
        self.btn_test.setText("测试中……")
        try:
            self.cfg = Config.load()
            reply = CloudLLM(self.cfg, self.cmb_provider.currentText()).probe()
            QMessageBox.information(self, "连接正常", f"模型已响应：{reply[:40]}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "连接失败", str(exc))
        finally:
            self.btn_test.setEnabled(True)
            self.btn_test.setText("测试连接")
