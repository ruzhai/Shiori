
import ctypes
import os
import sys
import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from PyQt5.QtCore import QSize, Qt, QObject, QThread, pyqtSignal, QStandardPaths
from PyQt5.QtGui import QIcon, QFont, QFontDatabase, QFontInfo
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QWidget,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QPushButton,
)

from main import AgentSettings, SYSTEM_PROMPT, create_file_agent, list_threads, get_thread_messages, delete_thread

try:
    # 用于拿到 token/tool 事件，实现"流式输出 + 运行日志"
    from langchain_core.callbacks import BaseCallbackHandler
except Exception:  # pragma: no cover
    BaseCallbackHandler = object  # type: ignore[misc,assignment]


def resource_path(relative_path: str) -> str:
    """返回资源文件的可访问路径（兼容 PyInstaller 打包后的 _MEIPASS 目录）。"""

    # PyInstaller 打包后会把资源解压到临时目录，并挂在 sys._MEIPASS 上
    base_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return str(base_dir / relative_path)


def load_embedded_fonts() -> list[str]:
    """加载随程序分发的字体文件，并返回"成功注册"的字体 family 列表。

    说明：
    - 这里的"内置"指把字体文件随程序一起分发（源码运行时放在 assets/fonts 下，
      打包时通过 PyInstaller 的 --add-data 一并打进可执行文件）。
    - 只要成功 addApplicationFont，Qt 就可以通过 font-family 使用该字体。
    """

    # 你可以把字体文件放到项目的 assets/fonts/ 目录下
    fonts_dir = resource_path(os.path.join("assets", "fonts"))

    # 常见建议文件名（你可以自行替换/增删，只要是 .ttf/.otf 都行）
    candidates = [
        "Inter-Regular.ttf",
        "Inter-SemiBold.ttf",
        "Inter-Bold.ttf",
        "HarmonyOS_Sans_SC_Regular.ttf",
        "HarmonyOS_Sans_SC_Medium.ttf",
        "HarmonyOS_Sans_SC_Bold.ttf",
    ]

    registered_families: list[str] = []

    for name in candidates:
        path = os.path.join(fonts_dir, name)
        if not os.path.exists(path):
            continue

        font_id = QFontDatabase.addApplicationFont(path)
        if font_id == -1:
            continue

        families = QFontDatabase.applicationFontFamilies(font_id)
        registered_families.extend(families)

    # 去重但保持相对稳定的顺序
    seen: set[str] = set()
    unique: list[str] = []
    for fam in registered_families:
        if fam in seen:
            continue
        seen.add(fam)
        unique.append(fam)

    return unique


def _app_data_dir() -> str:
    base = QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation)
    os.makedirs(base, exist_ok=True)
    return base


def _settings_file_path() -> str:
    return os.path.join(_app_data_dir(), "shiori_settings.json")


def _db_path() -> str:
    return os.path.join(_app_data_dir(), "shiori_history.db")


def _titles_file_path() -> str:
    return os.path.join(_app_data_dir(), "shiori_titles.json")


def _load_titles() -> dict:
    path = _titles_file_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_title(thread_id: str, title: str) -> None:
    titles = _load_titles()
    if thread_id not in titles:
        titles[thread_id] = title
        try:
            with open(_titles_file_path(), "w", encoding="utf-8") as f:
                json.dump(titles, f, ensure_ascii=False)
        except Exception:
            pass


def load_user_settings() -> dict[str, Any]:
    """读取用户设置（不存在则返回默认值）。"""

    defaults: dict[str, Any] = {
        "api_key": "",
        "base_url": "",
        "model": "",
        "temperature": 0.0,
        "system_prompt": SYSTEM_PROMPT,
        "streaming": True,
        "tools_list_directory": True,
        "tools_read_file": True,
        "tools_search_in_files": True,
        "show_run_log": True,
    }

    path = _settings_file_path()
    if not os.path.exists(path):
        return defaults

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            defaults.update(data)
    except Exception:
        pass

    return defaults


def save_user_settings(data: dict[str, Any]) -> None:
    """保存用户设置到本地 JSON。"""

    path = _settings_file_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def settings_to_agent_settings(data: dict[str, Any]) -> AgentSettings:
    """把 GUI 的 settings dict 转为 main.AgentSettings。"""

    return AgentSettings(
        api_key=(data.get("api_key") or None),
        base_url=(data.get("base_url") or None),
        model=(data.get("model") or None),
        temperature=float(data.get("temperature", 0.0) or 0.0),
        system_prompt=str(data.get("system_prompt") or SYSTEM_PROMPT),
        tools_list_directory=bool(data.get("tools_list_directory", True)),
        tools_read_file=bool(data.get("tools_read_file", True)),
        tools_search_in_files=bool(data.get("tools_search_in_files", True)),
        streaming=bool(data.get("streaming", True)),
    )


class SettingsDialog(QDialog):
    """设置对话框：模型 / 提示词 / 工具开关。"""

    def __init__(self, settings: dict[str, Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setModal(True)
        self.resize(760, 560)

        self._settings = dict(settings)

        root = QVBoxLayout(self)
        tabs = QTabWidget()
        root.addWidget(tabs, stretch=1)

        tab_model = QWidget()
        form = QFormLayout(tab_model)
        form.setLabelAlignment(Qt.AlignLeft)

        self.api_key_edit = QLineEdit(self._settings.get("api_key", ""))
        self.api_key_edit.setPlaceholderText("例如：sk-...（会保存在本机配置文件）")
        self.api_key_edit.setEchoMode(QLineEdit.Password)

        self.base_url_edit = QLineEdit(self._settings.get("base_url", ""))
        self.base_url_edit.setPlaceholderText(
            "例如：https://api.openai.com/v1 或你的代理地址")

        self.model_edit = QLineEdit(self._settings.get("model", ""))
        self.model_edit.setPlaceholderText(
            "例如：gpt-4.1-mini / gemini-2.0-flash / ...")

        self.temperature_spin = QDoubleSpinBox()
        self.temperature_spin.setRange(0.0, 2.0)
        self.temperature_spin.setSingleStep(0.1)
        self.temperature_spin.setValue(
            float(self._settings.get("temperature", 0.0) or 0.0))

        self.streaming_checkbox = QCheckBox("启用流式输出（推荐）")
        self.streaming_checkbox.setChecked(
            bool(self._settings.get("streaming", True)))

        form.addRow("API Key", self.api_key_edit)
        form.addRow("Base URL", self.base_url_edit)
        form.addRow("Model", self.model_edit)
        form.addRow("Temperature", self.temperature_spin)
        form.addRow("", self.streaming_checkbox)

        tabs.addTab(tab_model, "模型")

        tab_prompt = QWidget()
        prompt_layout = QVBoxLayout(tab_prompt)
        prompt_layout.setContentsMargins(10, 10, 10, 10)
        prompt_layout.setSpacing(8)
        tip = QLabel("SYSTEM_PROMPT（会影响助手的行为、风格与工具使用习惯）")
        tip.setObjectName("SettingTip")
        prompt_layout.addWidget(tip)
        self.prompt_edit = QPlainTextEdit(
            str(self._settings.get("system_prompt") or SYSTEM_PROMPT))
        self.prompt_edit.setPlaceholderText("在这里输入你自己的系统提示词...")
        prompt_layout.addWidget(self.prompt_edit, stretch=1)
        tabs.addTab(tab_prompt, "提示词")

        tab_tools = QWidget()
        tools_layout = QVBoxLayout(tab_tools)
        tools_layout.setContentsMargins(10, 10, 10, 10)
        tools_layout.setSpacing(10)
        tools_title = QLabel("启用工具（关闭后助手将无法执行对应的文件操作）")
        tools_title.setObjectName("SettingTip")
        tools_layout.addWidget(tools_title)

        self.tool_list_dir = QCheckBox("列出目录（list_directory）")
        self.tool_list_dir.setChecked(
            bool(self._settings.get("tools_list_directory", True)))
        self.tool_read_file = QCheckBox("读取文件（read_file）")
        self.tool_read_file.setChecked(
            bool(self._settings.get("tools_read_file", True)))
        self.tool_search = QCheckBox("全文搜索（search_in_files）")
        self.tool_search.setChecked(
            bool(self._settings.get("tools_search_in_files", True)))

        tools_layout.addWidget(self.tool_list_dir)
        tools_layout.addWidget(self.tool_read_file)
        tools_layout.addWidget(self.tool_search)
        tools_layout.addStretch(1)

        tabs.addTab(tab_tools, "工具")

        buttons = QDialogButtonBox(
            QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def get_settings(self) -> dict[str, Any]:
        data = dict(self._settings)
        data["api_key"] = self.api_key_edit.text().strip()
        data["base_url"] = self.base_url_edit.text().strip()
        data["model"] = self.model_edit.text().strip()
        data["temperature"] = float(self.temperature_spin.value())
        data["streaming"] = bool(self.streaming_checkbox.isChecked())
        data["system_prompt"] = self.prompt_edit.toPlainText(
        ).strip() or SYSTEM_PROMPT
        data["tools_list_directory"] = bool(self.tool_list_dir.isChecked())
        data["tools_read_file"] = bool(self.tool_read_file.isChecked())
        data["tools_search_in_files"] = bool(self.tool_search.isChecked())
        return data


class ChatBubble(QFrame):
    """可更新文本的聊天气泡（支持流式增量更新）。"""

    def __init__(self, role: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.role = role
        self.setObjectName("BubbleUser" if role ==
                           "user" else "BubbleAssistant")
        self._text = ""

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.card = QFrame()
        self.card.setObjectName(
            "BubbleCardUser" if role == "user" else "BubbleCardAssistant")
        self.card.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(14, 10, 14, 10)
        card_layout.setSpacing(6)

        self.body = QLabel("")
        self.body.setObjectName("BubbleBody")
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card_layout.addWidget(self.body)

        if role == "user":
            outer.addStretch(1)
            outer.addWidget(self.card, 0, Qt.AlignRight)
        else:
            outer.addWidget(self.card, 0, Qt.AlignLeft)
            outer.addStretch(1)

    def set_text(self, text: str) -> None:
        self._text = text
        self.body.setText(text)

    def append_text(self, text: str) -> None:
        self._text += text
        self.body.setText(self._text)

    @property
    def text(self) -> str:
        return self._text


class _QtCallbackHandler(BaseCallbackHandler):  # type: ignore[misc,valid-type]
    """把 LangChain 回调事件转成 GUI 可用的 token/log 信号。"""

    def __init__(self, emit_token: Callable[[str], None], emit_log: Callable[[str], None], should_stop: Callable[[], bool]) -> None:
        super().__init__()
        self._emit_token = emit_token
        self._emit_log = emit_log
        self._should_stop = should_stop

    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        if self._should_stop():
            return
        if token:
            self._emit_token(token)

    def on_tool_start(self, serialized: dict[str, Any], input_str: str, **kwargs: Any) -> None:
        name = serialized.get("name") or serialized.get("id") or "tool"
        self._emit_log(f"[tool:start] {name}\n输入: {input_str}\n\n")

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        out = str(output)
        if len(out) > 2000:
            out = out[:2000] + "\n...（已截断）"
        self._emit_log(f"[tool:end]\n输出:\n{out}\n\n")


class AgentWorker(QObject):
    """后台执行 agent，避免阻塞 UI。"""

    token = pyqtSignal(str)
    log = pyqtSignal(str)
    finished = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, agent: Any, config: dict[str, Any], user_text: str) -> None:
        super().__init__()
        self._agent = agent
        self._config = dict(config)
        self._user_text = user_text
        self._stop = False

    def request_stop(self) -> None:
        self._stop = True

    def _should_stop(self) -> bool:
        return self._stop

    def run(self) -> None:
        start = time.time()

        def emit_token(t: str) -> None:
            self.token.emit(t)

        def emit_log(t: str) -> None:
            self.log.emit(t)

        callbacks: list[Any] = []
        try:
            callbacks.append(_QtCallbackHandler(
                emit_token, emit_log, self._should_stop))
        except Exception:
            callbacks = []

        cfg = dict(self._config)
        if callbacks:
            cfg["callbacks"] = callbacks

        emit_log(f"[run:start] 用户输入: {self._user_text}\n\n")

        try:
            resp = self._agent.invoke(
                {"messages": [{"role": "user", "content": self._user_text}]},
                config={**cfg, "recursion_limit": 50},
            )
            structured = resp.get("structured_response")
            if structured is not None:
                answer = getattr(structured, "answer", str(structured))
            else:
                answer = str(resp)

            elapsed = time.time() - start
            emit_log(f"[run:end] 耗时: {elapsed:.2f}s\n\n")
            self.finished.emit(answer)
        except Exception as e:
            elapsed = time.time() - start
            emit_log(f"[run:error] 耗时: {elapsed:.2f}s\n\n")
            self.failed.emit(str(e))


class FileAgentWindow(QWidget):
    """GUI：左侧对话，右侧运行详情（工具调用/日志），顶部提供设置入口。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.Window)
        self.setWindowTitle("Shiori")
        self.is_dark_mode = False

        self.settings = load_user_settings()
        self._db_path = _db_path()
        self._current_thread_id: str = str(uuid.uuid4())

        self._rebuild_agent()

        self._worker_thread: Optional[QThread] = None
        self._worker: Optional[AgentWorker] = None
        self._current_assistant_bubble: Optional[ChatBubble] = None
        self._received_stream_token = False

        self._init_ui()
        self._apply_theme()
        self._refresh_session_list()

        self._append_assistant(
            "你好，我是 Shiori, 一个agent助手。\n"
            "提示：设置里可以填写 API / 自定义 SYSTEM_PROMPT / 选择启用哪些工具。"
        )

    def _rebuild_agent(self) -> None:
        agent_settings = settings_to_agent_settings(self.settings)
        try:
            self.agent, self.config = create_file_agent(
                thread_id=self._current_thread_id,
                settings=agent_settings,
                callbacks=None,
                db_path=self._db_path,
            )
        except Exception as e:
            self.agent, self.config = None, {}
            self.settings["_last_agent_error"] = str(e)

    def _refresh_session_list(self) -> None:
        self.session_list.clear()
        titles = _load_titles()
        for tid in list_threads(self._db_path):
            label = titles.get(tid) or tid[:8] + "..."
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, tid)
            self.session_list.addItem(item)
        for i in range(self.session_list.count()):
            if self.session_list.item(i).data(Qt.UserRole) == self._current_thread_id:
                self.session_list.setCurrentRow(i)
                break

    def _session_context_menu(self, pos) -> None:
        from PyQt5.QtWidgets import QMenu
        item = self.session_list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        action = menu.addAction("删除此会话")
        if menu.exec_(self.session_list.mapToGlobal(pos)) == action:
            self._delete_session_by_id(item.data(Qt.UserRole))

    def _delete_session_by_id(self, tid: str) -> None:
        delete_thread(self._db_path, tid)
        titles = _load_titles()
        titles.pop(tid, None)
        try:
            with open(_titles_file_path(), "w", encoding="utf-8") as f:
                json.dump(titles, f, ensure_ascii=False)
        except Exception:
            pass
        if tid == self._current_thread_id:
            self._new_session()
        else:
            self._refresh_session_list()

    def _clear_all_sessions(self) -> None:
        from PyQt5.QtWidgets import QMessageBox
        if QMessageBox.question(self, "确认", "清空所有会话历史？") != QMessageBox.Yes:
            return
        for tid in list_threads(self._db_path):
            delete_thread(self._db_path, tid)
        try:
            with open(_titles_file_path(), "w", encoding="utf-8") as f:
                json.dump({}, f)
        except Exception:
            pass
        self._new_session()

    def _delete_session(self) -> None:
        item = self.session_list.currentItem()
        if item is not None:
            self._delete_session_by_id(item.data(Qt.UserRole))

    def _new_session(self) -> None:
        self._current_thread_id = str(uuid.uuid4())
        if self.agent is not None:
            self.config = {"configurable": {"thread_id": self._current_thread_id}}
        else:
            self._rebuild_agent()
        self._clear_chat()
        self._refresh_session_list()
        self._append_assistant("新会话已开始。")

    def _on_session_selected(self, item: QListWidgetItem) -> None:
        tid = item.data(Qt.UserRole)
        if tid == self._current_thread_id:
            return
        self._current_thread_id = tid
        if self.agent is not None:
            self.config = {"configurable": {"thread_id": tid}}
        else:
            self._rebuild_agent()
        self._clear_chat()
        msgs = get_thread_messages(self._db_path, tid)
        for msg in msgs:
            if msg["role"] == "user":
                self._append_user(msg["content"])
            elif msg["role"] == "assistant" and msg["content"].strip():
                self._append_assistant(msg["content"])

    def _clear_chat(self) -> None:
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

    def _toggle_theme(self) -> None:
        self.is_dark_mode = not self.is_dark_mode
        self._apply_theme()

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(10)

        topbar = QFrame()
        topbar.setObjectName("TopBar")
        top_layout = QHBoxLayout(topbar)
        top_layout.setContentsMargins(14, 10, 14, 10)
        top_layout.setSpacing(10)

        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(0)
        top_layout.addLayout(title_box)
        top_layout.addStretch(1)

        self.model_chip = QLabel(self._model_chip_text())
        self.model_chip.setObjectName("ModelChip")
        top_layout.addWidget(self.model_chip)

        self.btn_toggle_log = QPushButton("运行详情")
        self.btn_toggle_log.setObjectName("TopButton")
        self.btn_toggle_log.clicked.connect(self._toggle_log_panel)
        top_layout.addWidget(self.btn_toggle_log)

        self.btn_settings = QPushButton("设置")
        self.btn_settings.setObjectName("TopButtonPrimary")
        self.btn_settings.clicked.connect(self._open_settings)
        top_layout.addWidget(self.btn_settings)

        self.btn_theme = QPushButton("🌓")
        self.btn_theme.setObjectName("TopButton")
        self.btn_theme.setFixedWidth(40)
        self.btn_theme.clicked.connect(self._toggle_theme)
        top_layout.addWidget(self.btn_theme)

        root.addWidget(topbar)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("MainSplitter")

        # 会话列表面板
        session_panel = QFrame()
        session_panel.setObjectName("SessionPanel")
        session_layout = QVBoxLayout(session_panel)
        session_layout.setContentsMargins(8, 8, 8, 8)
        session_layout.setSpacing(6)

        session_header = QHBoxLayout()
        session_title = QLabel("会话")
        session_title.setObjectName("LogTitle")
        session_header.addWidget(session_title)
        session_header.addStretch(1)
        self.btn_new_session = QPushButton("新建")
        self.btn_new_session.setObjectName("TopButton")
        self.btn_new_session.clicked.connect(self._new_session)
        session_header.addWidget(self.btn_new_session)
        session_layout.addLayout(session_header)

        self.session_list = QListWidget()
        self.session_list.setObjectName("SessionList")
        self.session_list.itemClicked.connect(self._on_session_selected)
        self.session_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.session_list.customContextMenuRequested.connect(self._session_context_menu)
        session_layout.addWidget(self.session_list, stretch=1)

        self.btn_clear_all_sessions = QPushButton("清空全部")
        self.btn_clear_all_sessions.setObjectName("TopButton")
        self.btn_clear_all_sessions.clicked.connect(self._clear_all_sessions)
        session_layout.addWidget(self.btn_clear_all_sessions)

        splitter.addWidget(session_panel)

        left = QFrame()
        left.setObjectName("LeftPanel")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        self.chat_scroll = QScrollArea()
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setObjectName("ChatScroll")
        self.chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.chat_container = QWidget()
        self.chat_container.setObjectName("ChatContainer")
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(10, 10, 10, 10)
        self.chat_layout.setSpacing(10)
        self.chat_layout.addStretch(1)

        self.chat_scroll.setWidget(self.chat_container)
        left_layout.addWidget(self.chat_scroll, stretch=1)

        input_card = QFrame()
        input_card.setObjectName("InputCard")
        input_layout = QHBoxLayout(input_card)
        input_layout.setContentsMargins(12, 10, 12, 10)
        input_layout.setSpacing(10)

        self.input_edit = QLineEdit()
        self.input_edit.setObjectName("InputEdit")
        self.input_edit.setPlaceholderText(
            "输入你的需求（例：列出 D:/software 目录）")
        self.input_edit.returnPressed.connect(self._on_send_clicked)

        self.btn_stop = QPushButton("停止")
        self.btn_stop.setObjectName("StopButton")
        self.btn_stop.clicked.connect(self._on_stop_clicked)
        self.btn_stop.setEnabled(False)

        self.send_button = QPushButton("发送")
        self.send_button.setObjectName("SendButton")
        self.send_button.clicked.connect(self._on_send_clicked)

        input_layout.addWidget(self.input_edit, stretch=1)
        input_layout.addWidget(self.btn_stop)
        input_layout.addWidget(self.send_button)
        left_layout.addWidget(input_card)

        splitter.addWidget(left)

        self.log_panel = QFrame()
        self.log_panel.setObjectName("LogPanel")
        log_layout = QVBoxLayout(self.log_panel)
        log_layout.setContentsMargins(10, 10, 10, 10)
        log_layout.setSpacing(8)

        log_header = QHBoxLayout()
        log_title = QLabel("运行详情")
        log_title.setObjectName("LogTitle")
        log_header.addWidget(log_title)
        log_header.addStretch(1)
        btn_clear = QPushButton("清空")
        btn_clear.setObjectName("TopButton")
        btn_clear.clicked.connect(lambda: self.log_view.clear())
        log_header.addWidget(btn_clear)
        log_layout.addLayout(log_header)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("LogView")
        self.log_view.setPlaceholderText(
            "这里会显示可验证的执行轨迹：\n"
            "- 工具调用（开始/结束/输出）\n"
            "- 本轮耗时\n\n"
        )
        log_layout.addWidget(self.log_view, stretch=1)

        splitter.addWidget(self.log_panel)
        splitter.setStretchFactor(0, 1)  # session panel
        splitter.setStretchFactor(1, 3)  # chat panel
        splitter.setStretchFactor(2, 2)  # log panel

        root.addWidget(splitter, stretch=1)

        if not bool(self.settings.get("show_run_log", True)):
            self.log_panel.hide()

        # 如果 agent 初始化失败（未填 API 等），给用户一个明确提示并禁用发送
        last_err = self.settings.pop("_last_agent_error", "")
        if last_err:
            self._log(f"[init] Agent 初始化失败：{last_err}\n\n")
            self._append_assistant(
                "我还没准备好：请先点右上角设置，填写 API Key / Base URL / Model，然后再开始对话。")
            self._set_busy(True)
            self.btn_stop.setEnabled(False)

    def _apply_theme(self) -> None:
        """Chatbox 风格优化"""
        if self.is_dark_mode:
            bg_main = "#242424"
            bg_card = "#2e2e2e"
            bg_input = "#2e2e2e"
            border_color = "#3e3e3e"
            text_primary = "#e0e0e0"
            text_secondary = "#a0a0a0"
            bubble_user_bg = "#1971c2"
            bubble_user_text = "#ffffff"
        else:
            bg_main = "#ffffff"
            bg_card = "#f8f8f8"
            bg_input = "#f8f8f8"
            border_color = "#e0e0e0"
            text_primary = "#2e2e2e"
            text_secondary = "#6e6e6e"
            bubble_user_bg = "#228be6"
            bubble_user_text = "#ffffff"

        self.setStyleSheet(f"""
            QWidget {{
                background: {bg_main};
                color: {text_primary};
                font-family: -apple-system, "Segoe UI", "Microsoft YaHei UI", sans-serif;
                font-size: 13px;
            }}

            /* 分割线 */
            QSplitter::handle {{
                background: {border_color};
            }}

            /* 顶部工具栏 */
            #TopBar {{
                background: {bg_main};
                border-bottom: 1px solid {border_color};
            }}

            /* 按钮：边框线风格 */
            #TopButton, #TopButtonPrimary, #StopButton, #SendButton {{
                background: {bg_main};
                border: 1px solid {border_color};
                border-radius: 4px;
                padding: 4px 12px;
                color: {text_primary};
            }}
            #SendButton {{
                background: {text_primary};
                color: {bg_main};
            }}

            /* 聊天气泡核心逻辑 */
            #BubbleCardAssistant {{
                background: {bg_card};
                border: 1px solid {border_color};
                border-radius: 8px;
            }}

            #BubbleCardUser {{
                background: {bubble_user_bg};
                border-radius: 8px;
            }}

            /* 关键点：强制用户气泡内的文字颜色 */
            #BubbleCardUser #BubbleBody {{
                background: transparent !important;
                color: {bubble_user_text} !important;
            }}

            /* 助手气泡文字颜色 */
            #BubbleCardAssistant #BubbleBody {{
                color: {text_primary};
            }}

            /* 输入框区域 */
            #InputCard {{
                border-top: 1px solid {border_color};
                background: {bg_main};
            }}
            #InputEdit {{
                background: {bg_input};
                border: 1px solid {border_color};
                border-radius: 6px;
                padding: 8px;
                color: {text_primary};
            }}

            /* 会话面板 */
            #SessionPanel {{
                border-right: 1px solid {border_color};
            }}
            #SessionList {{
                background: {bg_main};
                color: {text_primary};
                border: 1px solid {border_color};
                border-radius: 4px;
            }}
            #SessionList::item {{
                padding: 8px;
                border-radius: 4px;
            }}
            #SessionList::item:selected {{
                background: {border_color};
            }}

            /* 运行详情日志 */
            #LogPanel {{
                border-left: 1px solid {border_color};
            }}
            #LogView {{
                background: {bg_main};
                color: {text_secondary};
                border: none;
            }}
            
            /* 滚动条极简化 * /
            QScrollBar: vertical {{
                border: none;
                background: {bg_main};
                width: 8px;
            }}
            QScrollBar: : handle: vertical {{
                background: {border_color};
                border-radius: 4px;
                min-height: 20px;
            }}
            QScrollBar: : add-line: vertical, QScrollBar: : sub-line: vertical {{border: none; background: none; }}
        """)

    def _model_chip_text(self) -> str:
        m = (self.settings.get("model") or "").strip()
        return m if m else "未设置模型"

    def _toggle_log_panel(self) -> None:
        visible = self.log_panel.isVisible()
        self.log_panel.setVisible(not visible)
        self.settings["show_run_log"] = not visible
        save_user_settings(self.settings)

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.settings, parent=self)
        if dlg.exec_() == QDialog.Accepted:
            self.settings = dlg.get_settings()
            save_user_settings(self.settings)
            self.model_chip.setText(self._model_chip_text())
            self._rebuild_agent()
            if self.agent is None:
                self._log("[settings] 设置未完整，Agent 仍无法初始化。\n\n")
                self._append_assistant(
                    "当前未初始化模型连接。请先在设置里填写 API Key / Base URL / Model。")
                self._set_busy(True)
                self.btn_stop.setEnabled(False)
            else:
                self._log("[settings] 已应用新设置，并重建 Agent。\n\n")
                self._set_busy(False)

    def _log(self, text: str) -> None:
        self.log_view.moveCursor(self.log_view.textCursor().End)
        self.log_view.insertPlainText(text)
        self.log_view.moveCursor(self.log_view.textCursor().End)

    def _scroll_to_bottom(self) -> None:
        bar = self.chat_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _append_user(self, text: str) -> None:
        bubble = ChatBubble("user")
        bubble.set_text(text)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble)
        self._scroll_to_bottom()

    def _append_assistant(self, text: str) -> ChatBubble:
        bubble = ChatBubble("assistant")
        bubble.set_text(text)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble)
        self._scroll_to_bottom()
        return bubble

    def _set_busy(self, busy: bool) -> None:
        self.input_edit.setDisabled(busy)
        self.send_button.setDisabled(busy)
        self.btn_stop.setEnabled(busy)

    def _on_stop_clicked(self) -> None:
        if self._worker is not None:
            self._worker.request_stop()
            self._log("[ui] 已请求停止（将忽略后续流式 token）。\n\n")
        self._set_busy(False)

    def _on_send_clicked(self) -> None:
        user_text = self.input_edit.text().strip()
        if not user_text:
            return
        if self.agent is None:
            self._append_assistant(
                "当前未初始化模型连接。请先在设置里填写 API Key / Base URL / Model。")
            return

        self._append_user(user_text)
        _save_title(self._current_thread_id, user_text[:20])
        self.input_edit.clear()

        self._current_assistant_bubble = self._append_assistant("正在生成...")
        self._current_assistant_bubble.set_text("")
        self._received_stream_token = False

        self._set_busy(True)

        self._worker_thread = QThread()
        self._worker = AgentWorker(self.agent, self.config, user_text)
        self._worker.moveToThread(self._worker_thread)

        self._worker_thread.started.connect(self._worker.run)
        self._worker.token.connect(self._on_stream_token)
        self._worker.log.connect(self._log)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.finished.connect(self._cleanup_worker)
        self._worker.failed.connect(self._cleanup_worker)

        self._worker_thread.start()

    def _on_stream_token(self, token: str) -> None:
        if self._current_assistant_bubble is None or not token:
            return
        self._received_stream_token = True
        self._current_assistant_bubble.append_text(token)
        self._scroll_to_bottom()

    def _animate_to_full_text(self, full_text: str) -> None:
        if self._current_assistant_bubble is None:
            return

        from PyQt5.QtCore import QTimer

        self._current_assistant_bubble.set_text("")
        idx = 0
        step = 12
        timer = QTimer(self)

        def tick() -> None:
            nonlocal idx
            if self._current_assistant_bubble is None:
                timer.stop()
                return
            if idx >= len(full_text):
                timer.stop()
                return
            self._current_assistant_bubble.append_text(
                full_text[idx: idx + step])
            idx += step
            self._scroll_to_bottom()

        timer.timeout.connect(tick)
        timer.start(15)

    def _on_worker_finished(self, answer: str) -> None:
        if self._current_assistant_bubble is not None:
            if not self._received_stream_token:
                self._animate_to_full_text(answer)
            else:
                if len(self._current_assistant_bubble.text.strip()) < len(answer.strip()):
                    self._current_assistant_bubble.set_text(answer)
        self._set_busy(False)
        self._refresh_session_list()

    def _on_worker_failed(self, err: str) -> None:
        if self._current_assistant_bubble is not None:
            self._current_assistant_bubble.set_text(f"调用失败：{err}")
        self._set_busy(False)

    def _cleanup_worker(self, *_: Any) -> None:
        if self._worker_thread is not None:
            self._worker_thread.quit()
            self._worker_thread.wait(1500)
        self._worker_thread = None
        self._worker = None


def main() -> None:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    if sys.platform == "win32":
        try:
            # 自定义唯一 ID（格式任意，建议用公司名.产品名.版本）
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "shiori.1.0")
        except AttributeError:
            pass  # 低版本 Windows 可能没有此函数

    app = QApplication(sys.argv)
    icon_path = os.path.join(os.path.dirname(
        __file__), "assets", "icon", "icon.svg")
    app.setWindowIcon(QIcon(icon_path))
    window = FileAgentWindow()

    screen = app.primaryScreen()
    if screen is not None:
        available_geom = screen.availableGeometry()
        target_width = int(available_geom.width() * 0.72)
        target_height = int(available_geom.height() * 0.72)
        window.resize(target_width, target_height)
        window.move(
            available_geom.x() + (available_geom.width() - target_width) // 2,
            available_geom.y() + (available_geom.height() - target_height) // 2,
        )

    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
