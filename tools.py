import os
import queue
import subprocess
import threading

from langchain.tools import tool


# =========================
# 确认上下文（线程安全）
# =========================

class ConfirmContext:
    """Agent 线程与 SSE 主线程之间的命令确认通信桥梁。

    Agent 线程调用 request_confirm() 阻塞等待。
    SSE 主线程调用 poll_pending() 获取待确认请求，yield 给前端。
    前端确认后通过 /api/confirm 调用 resolve() 解阻塞。
    """

    def __init__(self, whitelist: set[str] | None = None, timeout: float = 120.0):
        self._queue: queue.Queue = queue.Queue()
        self._events: dict[int, threading.Event] = {}
        self._results: dict[int, bool] = {}
        self._lock = threading.Lock()
        self._counter: int = 0
        self._timeout = timeout
        self._whitelist: set[str] = set(whitelist or [])

    @staticmethod
    def _extract_pattern(command: str) -> str:
        """提取命令模式：取第一个词，如 'dir "path"' → 'dir'。"""
        parts = command.strip().split(maxsplit=1)
        return parts[0].lower() if parts else ""

    def _is_whitelisted(self, command: str) -> bool:
        """检查命令是否命中白名单（前缀匹配）。"""
        cmd_lower = command.strip().lower()
        for pattern in self._whitelist:
            if cmd_lower.startswith(pattern):
                return True
        return False

    def add_whitelist(self, pattern: str) -> None:
        """添加一个模式到白名单。"""
        with self._lock:
            self._whitelist.add(pattern.lower())

    def request_confirm(self, command: str, working_dir: str) -> bool:
        """Agent 线程调用，先查白名单，未命中则阻塞等待用户确认。"""
        # 白名单命中 → 直接批准
        if self._is_whitelisted(command):
            return True

        with self._lock:
            self._counter += 1
            req_id = self._counter
            event = threading.Event()
            self._events[req_id] = event

        self._queue.put({
            "id": req_id,
            "command": command,
            "working_dir": working_dir,
        })

        resolved = event.wait(timeout=self._timeout)

        with self._lock:
            result = self._results.pop(req_id, False) if resolved else False
            self._events.pop(req_id, None)
            self._results.pop(req_id, None)

        return result

    def poll_pending(self) -> dict | None:
        """非阻塞轮询，返回下一个待确认请求或 None。"""
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def resolve(self, req_id: int, approved: bool) -> None:
        """主线程调用（来自 /api/confirm），解除 Agent 线程阻塞。"""
        with self._lock:
            self._results[req_id] = approved
            event = self._events.get(req_id)
        if event:
            event.set()

    def is_resolved(self, req_id: int) -> bool:
        """检查某个请求是否已被处理。"""
        with self._lock:
            event = self._events.get(req_id)
        return event is None or event.is_set()

    def reject_all_pending(self) -> None:
        """拒绝所有等待中的请求（停止时调用）。"""
        with self._lock:
            for rid in list(self._events.keys()):
                self._results[rid] = False
                event = self._events.get(rid)
                if event:
                    event.set()


# 模块级确认上下文（由 api_server 注入）
_confirm_ctx: ConfirmContext | None = None


def set_confirm_context(ctx: ConfirmContext) -> None:
    global _confirm_ctx
    _confirm_ctx = ctx


def clear_confirm_context() -> None:
    global _confirm_ctx
    _confirm_ctx = None


# =========================
# 工具定义
# =========================


@tool
def run_command(command: str, working_dir: str = "") -> str:
    """在 Windows 命令行中执行一条命令并返回输出。

    所有命令执行前都会请求用户在界面上确认。

    参数：
    - command: 要执行的命令（字符串），例如 "dir D:\\projects" 或 "python --version"
    - working_dir: 工作目录（可选），默认为当前项目目录

    常用命令：
    - dir 路径 — 列出目录内容
    - type 文件 — 读取文件内容
    - findstr /s "关键字" "路径\\*" — 搜索文件内容
    - git status / git log — 查看 Git 状态
    - python --version / pip list — 查看 Python 环境

    注意：
    - 命令执行有输出上限，过长会被截断
    - 所有命令需用户确认后才会执行
    """

    cwd = working_dir if (working_dir and os.path.isdir(working_dir)) else os.getcwd()

    # —— 确认门 ——
    global _confirm_ctx
    if _confirm_ctx is not None:
        approved = _confirm_ctx.request_confirm(command, cwd)
        if not approved:
            return (
                "[已拒绝] 用户拒绝了该命令的执行。\n"
                f"被拒绝的命令：{command}"
            )
    else:
        return (
            "[已拒绝] 确认机制未启用，无法执行命令。\n"
            "请通过 Shiori 应用界面发起操作。"
        )

    # —— 执行 ——
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as e:
        return f"[错误] 无法执行命令：{e}"

    stdout = result.stdout or ""
    stderr = result.stderr or ""
    output_parts = [f"[工作目录] {cwd}"]
    if stdout:
        out = stdout[:8000]
        if len(stdout) > 8000:
            out += f"\n...（已截断，共 {len(stdout)} 字符）"
        output_parts.append(f"[stdout]\n{out}")
    if stderr:
        err = stderr[:2000]
        if len(stderr) > 2000:
            err += f"\n...（已截断，共 {len(stderr)} 字符）"
        output_parts.append(f"[stderr]\n{err}")
    if not stdout and not stderr:
        output_parts.append("(无输出)")

    output_parts.append(f"[exit code: {result.returncode}]")
    return "\n".join(output_parts)
