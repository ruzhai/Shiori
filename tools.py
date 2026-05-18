import os
import queue
import subprocess
import threading

from langchain.tools import tool

# ── PDF 阅读（PyMuPDF）──────────────────────────────
try:
    import fitz
except ImportError:
    fitz = None


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


# =========================
# 文件操作工具
# =========================


@tool
def list_directory(path: str) -> str:
    """列出某个目录下的文件和子目录。

    参数:
        path: 要查看的目录绝对路径。
    返回:
        一个可直接展示的文本列表，包含 [DIR]/[FILE] 标记及文件大小。
    """
    normalized = os.path.normpath(path)
    if normalized in (r'C:\\', r'D:\\', r'E:\\', '/', 'C:', 'D:', 'E:'):
        return "请提供具体的目录路径，而不是根目录。"

    if not os.path.exists(path):
        return f"目录不存在：{path}"
    if not os.path.isdir(path):
        return f"给定路径不是目录：{path}"

    try:
        entries = os.listdir(path)
    except Exception as e:
        return f"读取目录失败：{e}"

    if not entries:
        return f"目录为空：{path}"

    lines: list[str] = []
    for name in entries:
        full = os.path.join(path, name)
        if os.path.isdir(full):
            lines.append(f"[DIR]  {name}")
        else:
            try:
                size = os.path.getsize(full)
                lines.append(f"[FILE] {name}  ({size} bytes)")
            except OSError:
                lines.append(f"[FILE] {name}")

    return "目录内容：\n" + "\n".join(sorted(lines))


@tool
def read_file(path: str, max_chars: int = 4000, start: int = 0) -> str:
    """读取指定文本文件内容，用于分析和总结。

    参数:
        path: 文件绝对路径
        max_chars: 最多读取的字符数（默认 4000）
        start: 从第几个字符开始读取（默认 0，即从头开始）
    """

    if not os.path.exists(path):
        return f"文件不存在：{path}"
    if not os.path.isfile(path):
        return f"给定路径不是文件：{path}"

    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            all_content = f.read()
    except Exception as e:
        return f"读取文件失败：{e}"

    total = len(all_content)
    if start >= total:
        return f"文件路径：{path}\n文件总字符数：{total}，start={start} 已超出文件末尾，无更多内容。"

    chunk = all_content[start: start + max_chars]
    end = start + len(chunk)
    suffix = f"\n\n[已读取字符 {start}–{end} / 共 {total}。{'文件已读完。' if end >= total else f'如需继续，请用 start={end} 调用。'}]"
    return f"文件路径：{path}\n=== 内容开始 ===\n{chunk}{suffix}"


@tool
def search_in_files(root: str, keyword: str, max_results: int = 20) -> str:
    """在某个目录（包含子目录）下搜索包含指定关键字的文本文件。

    参数：
    - root: 起始搜索目录绝对路径
    - keyword: 要搜索的字符串
    - max_results: 最多返回多少条匹配结果
    """

    if not os.path.exists(root):
        return f"目录不存在：{root}"
    if not os.path.isdir(root):
        return f"给定路径不是目录：{root}"

    matches: list[str] = []
    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            if len(matches) >= max_results:
                break
            full_path = os.path.join(dirpath, filename)
            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line_no, line in enumerate(f, start=1):
                        if keyword in line:
                            snippet = line.strip()
                            matches.append(f"{full_path} (L{line_no}): {snippet[:200]}")
                            break
            except Exception:
                continue

    if not matches:
        return f"在目录 {root} 下未找到包含 {keyword!r} 的文本文件。"

    header = f"在目录 {root} 下找到包含 {keyword!r} 的文件（最多 {max_results} 条）："
    return header + "\n" + "\n".join(matches)


# =========================
# 学术搜索工具
# =========================

from semantic_scholar import create_connector, Paper

# 模块级 Semantic Scholar API key（由 api_server 在初始化时设置）
_scholar_api_key: str | None = None


def set_scholar_api_key(key: str | None) -> None:
    global _scholar_api_key
    _scholar_api_key = key


# PDF 阅读计数器（防止 Agent 无休止逐页读论文）
_pdf_read_count: int = 0
_PDF_READ_LIMIT = 3


def reset_pdf_counter() -> None:
    global _pdf_read_count
    _pdf_read_count = 0


# Scholar API 连续错误计数器（防止 Agent 限流后无限重试）
_scholar_error_streak: int = 0
_SCHOLAR_ERROR_LIMIT = 2

_FORCE_STOP_MSG = (
    "API 暂时限流。你已经获取了足够的论文摘要和引用信息，"
    "请立即基于已有数据撰写文献综述，禁止再调用任何搜索工具。"
)

# Scholar 工具总调用计数器（防止 Agent 无限搜索永不输出）
_scholar_call_count: int = 0
_SCHOLAR_CALL_LIMIT = 15

_SCHOLAR_TOTAL_LIMIT_MSG = (
    "已达学术搜索总次数上限。你已经获取了大量论文信息，"
    "请立即基于已有数据撰写文献综述，不要再调用任何搜索或查询工具。"
)


def reset_scholar_errors() -> None:
    global _scholar_error_streak
    _scholar_error_streak = 0


def _handle_scholar_error(last_error: str | None) -> str:
    """连续限流达到阈值时，返回强制停止消息；否则返回普通错误。"""
    global _scholar_error_streak
    _scholar_error_streak += 1
    if _scholar_error_streak >= _SCHOLAR_ERROR_LIMIT:
        return _FORCE_STOP_MSG
    return f"学术搜索服务暂时不可用（{last_error}）。请稍后再试。"


def reset_scholar_counter() -> None:
    global _scholar_call_count
    _scholar_call_count = 0


def _check_scholar_limit() -> str | None:
    """检查 scholar 工具总调用次数；超限返回强制停止消息，否则返回 None。"""
    global _scholar_call_count
    _scholar_call_count += 1
    if _scholar_call_count > _SCHOLAR_CALL_LIMIT:
        return _SCHOLAR_TOTAL_LIMIT_MSG
    return None


def _fmt_paper(p: Paper, idx: int = 0) -> str:
    authors = ", ".join(p.authors[:5])
    if len(p.authors) > 5:
        authors += " 等"
    lines = [
        f"{idx}. {p.title}" if idx else p.title,
        f"   作者: {authors}" if authors else "   作者: 未知",
        f"   年份: {p.year} | 引用: {p.citation_count}",
    ]
    if p.abstract:
        abstract_short = p.abstract[:200].replace("\n", " ")
        lines.append(f"   摘要: {abstract_short}...")
    if p.venue:
        lines.append(f"   发表: {p.venue}")
    if p.open_access_url:
        lines.append(f"   PDF: {p.open_access_url}")
    if p.url:
        lines.append(f"   链接: {p.url}")
    lines.append(f"   ID: {p.paper_id}")
    return "\n".join(lines)


@tool
def search_papers(query: str, limit: int = 5) -> str:
    """搜索学术论文。当用户想查找某个主题的论文时使用。

    参数:
        query: 搜索关键词（建议使用英文，如 "deep learning transformer"）
        limit: 返回的最大论文数量（默认5，最多10）
    返回:
        格式化的论文列表，包含标题、作者、年份、引用数、摘要和论文ID。
    """
    limit_msg = _check_scholar_limit()
    if limit_msg:
        return limit_msg
    conn = create_connector(api_key=_scholar_api_key)
    papers, ok = conn.search(query, limit=min(limit, 10))
    if not ok:
        return _handle_scholar_error(conn.last_error)
    reset_scholar_errors()
    if not papers:
        return f"未找到与 '{query}' 相关的论文。请尝试调整关键词。"
    lines = [f"搜索 '{query}' 的结果（共 {len(papers)} 篇）：\n"]
    for i, p in enumerate(papers, 1):
        lines.append(_fmt_paper(p, i))
        lines.append("")
    return "\n".join(lines)


@tool
def get_paper_details(paper_id: str) -> str:
    """获取指定论文的详细信息（完整摘要、作者、引用数等）。

    参数:
        paper_id: Semantic Scholar 论文 ID（从 search_papers 结果中获取）
    """
    limit_msg = _check_scholar_limit()
    if limit_msg:
        return limit_msg
    conn = create_connector(api_key=_scholar_api_key)
    paper, ok = conn.get_paper(paper_id.strip())
    if not ok:
        return _handle_scholar_error(conn.last_error)
    reset_scholar_errors()
    if paper is None:
        return f"未找到论文 ID 为 '{paper_id.strip()}' 的论文。请检查 ID 是否来自最近的搜索结果，或尝试重新搜索。"
    return "论文详情：\n\n" + _fmt_paper(paper)


@tool
def get_paper_citations(paper_id: str, limit: int = 5) -> str:
    """获取引用了某篇论文的其他论文（即哪些论文引用了这篇）。

    参数:
        paper_id: 目标论文的 Semantic Scholar ID
        limit: 返回的最大数量（默认5，最多10）
    """
    limit_msg = _check_scholar_limit()
    if limit_msg:
        return limit_msg
    conn = create_connector(api_key=_scholar_api_key)
    papers, ok = conn.get_citations(paper_id.strip(), limit=min(limit, 10))
    if not ok:
        return _handle_scholar_error(conn.last_error)
    reset_scholar_errors()
    if not papers:
        return f"未找到引用论文 ID 为 '{paper_id.strip()}' 的论文。"
    lines = [f"引用了论文 {paper_id.strip()} 的论文（共 {len(papers)} 篇）：\n"]
    for i, p in enumerate(papers, 1):
        lines.append(_fmt_paper(p, i))
        lines.append("")
    return "\n".join(lines)


@tool
def get_paper_references(paper_id: str, limit: int = 5) -> str:
    """获取某篇论文引用的参考文献（即这篇论文引用了哪些论文）。

    参数:
        paper_id: 目标论文的 Semantic Scholar ID
        limit: 返回的最大数量（默认5，最多10）
    """
    limit_msg = _check_scholar_limit()
    if limit_msg:
        return limit_msg
    conn = create_connector(api_key=_scholar_api_key)
    papers, ok = conn.get_references(paper_id.strip(), limit=min(limit, 10))
    if not ok:
        return _handle_scholar_error(conn.last_error)
    reset_scholar_errors()
    if not papers:
        return f"未找到论文 {paper_id.strip()} 的参考文献。"
    lines = [f"论文 {paper_id.strip()} 的参考文献（共 {len(papers)} 篇）：\n"]
    for i, p in enumerate(papers, 1):
        lines.append(_fmt_paper(p, i))
        lines.append("")
    return "\n".join(lines)


# =========================
# PDF 文献阅读工具
# =========================


def _is_url(path: str) -> bool:
    return path.startswith("http://") or path.startswith("https://")


def _download_pdf(url: str) -> str | None:
    """流式下载 PDF 到临时文件，返回临时文件路径。失败返回 None。"""
    import tempfile
    try:
        import requests as _requests
        resp = _requests.get(url, timeout=(10, 60), stream=True)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")

        fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
        first_chunk = True
        max_size = 50 * 1024 * 1024  # 50MB 上限
        total = 0
        with os.fdopen(fd, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    if first_chunk and not content_type.startswith("application/pdf") and not chunk.startswith(b"%PDF"):
                        resp.close()
                        os.unlink(tmp_path)
                        return None
                    first_chunk = False
                    total += len(chunk)
                    if total > max_size:
                        resp.close()
                        os.unlink(tmp_path)
                        return None
                    f.write(chunk)
        return tmp_path
    except Exception:
        return None


@tool
def read_pdf(path: str, max_chars: int = 8000, start: int = 0) -> str:
    """读取指定 PDF 文件内容，用于分析学术论文全文。支持本地路径和 URL。

    参数:
        path: PDF 文件的绝对路径或 HTTP/HTTPS URL
        max_chars: 最多读取的字符数（默认 8000，不要传小值）
        start: 从第几个字符开始读取（默认 0，即从头开始）

    注意: 如果是 URL，会自动下载到临时文件后读取。每个 URL 只尝试一次，失败后不要重试。
    """
    global _pdf_read_count
    _pdf_read_count += 1

    if _pdf_read_count > _PDF_READ_LIMIT:
        return (
            f"⚠️ 本次对话已读取 {_PDF_READ_LIMIT} 篇/次 PDF，达到上限。\n"
            "请基于已获取的论文摘要（get_paper_details）和已有信息直接撰写回答，"
            "不要再尝试读取更多 PDF。"
        )

    if fitz is None:
        return "PDF 阅读功能不可用：PyMuPDF 未安装。请运行 pip install PyMuPDF。"

    original_path = path
    tmp_file: str | None = None

    # URL 自动下载
    if _is_url(path):
        tmp_file = _download_pdf(path)
        if tmp_file is None:
            return f"无法下载 PDF：{path}\n可能原因：链接已失效、不是 PDF 文件、或网络不可达。请换一篇论文尝试，不要重试同一链接。"
        path = tmp_file

    if not os.path.exists(path):
        return f"PDF 文件不存在：{path}"
    if not os.path.isfile(path):
        return f"给定路径不是文件：{path}"
    if not path.lower().endswith('.pdf'):
        return f"文件不是 PDF 格式：{path}"

    try:
        doc = fitz.open(path)
    except Exception as e:
        return f"无法打开 PDF 文件：{e}"

    _stop_hint = (
        "\n\n⚠️ 你正在撰写文献综述。论文摘要（get_paper_details 返回的 abstract）已经包含足够信息。"
        "请立即停止读取 PDF，基于已获取的摘要开始撰写综述，不要再调用 read_pdf。"
    )

    try:
        total_pages = len(doc)
        all_text_parts: list[str] = []
        total_chars = 0

        for page_idx in range(total_pages):
            try:
                page = doc[page_idx]
                page_text = page.get_text()
                if page_text.strip():
                    part = f"[第{page_idx + 1}页]\n{page_text.strip()}"
                    all_text_parts.append(part)
                    total_chars += len(part)
                if total_chars >= start + max_chars:
                    partial_text = "\n\n".join(all_text_parts)
                    total_extracted = len(partial_text)
                    if start >= total_extracted:
                        return f"PDF 文件路径：{original_path}\n总页数：{total_pages}\n已提取字符数：{total_extracted}\nstart={start} 已超出末尾，无更多内容。{_stop_hint}"
                    chunk = partial_text[start: start + max_chars]
                    end = start + len(chunk)
                    return (
                        f"PDF 文件路径：{original_path}\n"
                        f"总页数：{total_pages}（已读取前 {page_idx + 1} 页）\n"
                        f"=== 内容开始 ===\n{chunk}\n\n"
                        f"[已读取字符 {start}–{end} / 已提取 {total_extracted}。"
                        f"{'文件已读完。' if end >= total_extracted else f'如需继续，请用 start={end} 调用。'}]{_stop_hint}"
                    )
            except Exception:
                all_text_parts.append(f"[第{page_idx + 1}页] (无法读取)")
    finally:
        doc.close()
        if tmp_file:
            try:
                os.unlink(tmp_file)
            except Exception:
                pass

    full_text = "\n\n".join(all_text_parts)
    total_extracted = len(full_text)

    if start >= total_extracted:
        return f"PDF 文件路径：{original_path}\n总页数：{total_pages}\n已提取总字符数：{total_extracted}\nstart={start} 已超出末尾，无更多内容。{_stop_hint}"

    chunk = full_text[start: start + max_chars]
    end = start + len(chunk)
    return (
        f"PDF 文件路径：{original_path}\n"
        f"总页数：{total_pages}\n"
        f"=== 内容开始 ===\n{chunk}\n\n"
        f"[已读取字符 {start}–{end} / 共 {total_extracted}（共 {total_pages} 页）。"
        f"{'文件已读完。' if end >= total_extracted else f'如需继续，请用 start={end} 调用。'}]{_stop_hint}"
    )


