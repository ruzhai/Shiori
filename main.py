import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Literal

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from tools import (
    run_command,
    list_directory,
    read_file,
    search_in_files,
    search_papers,
    get_paper_details,
    get_paper_citations,
    get_paper_references,
    read_pdf,
)

# =========================
# 系统提示 & 响应结构
# =========================

SYSTEM_PROMPT = """你是一个多功能智能助手，工作在 Windows 系统上，兼具科研文献分析和命令行操作能力。

## 核心能力

### 学术科研
- 使用 **search_papers** 在 Semantic Scholar 中搜索学术论文（关键词用英文，精简到 2-4 个核心词）
- 使用 **get_paper_details** 获取指定论文的详细摘要、作者、引用数等信息
- 使用 **get_paper_citations** 查看哪些论文引用了目标论文（追踪后续研究）
- 使用 **get_paper_references** 查看目标论文引用了哪些论文（追溯研究基础）
- 使用 **read_pdf** 读取 PDF 论文全文内容，按页提取文本

### 文件操作
- 使用 **list_directory** 列出目录内容
- 使用 **read_file** 读取文本文件内容
- 使用 **search_in_files** 在目录下搜索包含关键字的文件

### 命令行
- 使用 **run_command** 执行 Windows 命令（需用户确认）

## 回复决策流程

### 第1步：判断意图
  - 用户提出科研文献相关需求 → 使用学术工具链（search_papers → get_paper_details → get_paper_citations/references）
  - 用户要求列出目录/读取文件/搜索文件内容 → 使用文件工具
  - 用户要求执行命令/运行脚本/查询网络 → 使用 run_command
  - 用户问候、闲聊、概念性问题 → 直接回复

### 第2步：文献综述工作流（不遵守将导致任务失败）
  - **所有学术搜索工具（search_papers、get_paper_details、get_paper_citations、get_paper_references）累计最多调用 15 次，超过后工具会直接拒绝**
  - search_papers 最多调用 2 次，每次 limit=5。第 1 次拿到结果后挑最相关的 3-5 篇查详情
  - **不要在查完详情后又去搜第二轮——两轮搜索足够覆盖一个主题**
  - **不要对每篇论文都查引用和参考文献**——引用/参考文献工具仅在用户明确要求时使用
  - read_pdf 最多 3 次，写文献综述时完全不需要 read_pdf
  - **目标：用 8-12 次 scholar 调用完成搜索+详情，然后立即撰写综述**

### 第3步：限流处理
  - 任何工具返回"限流""重试耗尽""429"→ 停止搜索，用已有信息回答
  - 搜索返回空结果 → 告知用户，不要换关键词重搜
  - 连续 2 个工具返回错误 → 立即停止，基于已有信息回答

### 第4步：命令执行规则
  - run_command 执行前系统会自动弹出确认框，你无需在回复中提及
  - 对于明显危险的操作（format、diskpart、删除系统文件等），应主动提醒用户风险

## 搜索策略
- 使用 search_papers（Semantic Scholar），数据更全、引用信息更丰富
- 关键词始终使用英文，limit 使用默认值 5

## 输出格式
- 论文信息用结构化 Markdown 展示，包含标题、作者、年份、引用数、摘要要点
- 使用简体中文，语气专业友好

## 路径规则
- 文件路径由用户提供
- Windows 格式：D:\\xxx\\yyy"""


@dataclass
class AgentSettings:
    """创建 Agent/模型所需的可配置项。

    所有模型连接参数必须由 settings 显式提供，不依赖环境变量。
    """

    # 模型连接参数
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    temperature: float = 0

    # Semantic Scholar API key
    scholar_api_key: Optional[str] = None

    # 系统提示词
    system_prompt: str = SYSTEM_PROMPT

    # 工具开关
    tools_run_command: bool = True
    tools_list_directory: bool = True
    tools_read_file: bool = True
    tools_search_in_files: bool = True
    tools_search_papers: bool = True
    tools_get_paper_details: bool = True
    tools_get_paper_citations: bool = True
    tools_get_paper_references: bool = True
    tools_read_pdf: bool = True

    # 结构化输出开关（自动检测，也可手动覆盖）
    use_structured_output: bool = True

    # 流式输出
    streaming: bool = False


@dataclass
class ResponseFormat:
    """代理响应结构，用于 LangGraph 结构化输出。

    部分模型（reasoner 类）不支持 tool_choice 时自动关闭。
    """

    answer: str
    related_files: Optional[List[str]] = None


# =========================
# Provider 模型工厂
# =========================

# 每个 provider 的模型能力和限制
@dataclass
class _ProviderInfo:
    name: str
    model_keywords: list[str]  # 用于自动检测的关键词（匹配 model 名）
    url_keywords: list[str]  # 用于自动检测的关键词（匹配 base_url）
    supports_tool_choice: bool  # 是否支持 tool_choice 参数
    has_reasoning_content: bool  # 是否需要传递 reasoning_content
    streaming_param: Literal["init", "stream"]  # streaming 传递方式

    def matches(self, model: str, base_url: str) -> bool:
        m = model.lower()
        u = base_url.lower()
        return any(kw in m for kw in self.model_keywords) or any(
            kw in u for kw in self.url_keywords
        )


# Provider 注册表（按优先级排列，先匹配先使用）
_PROVIDERS: list[_ProviderInfo] = [
    _ProviderInfo(
        name="deepseek",
        model_keywords=["deepseek-v4", "deepseek-chat", "deepseek-reasoner"],
        url_keywords=["deepseek.com"],
        supports_tool_choice=True,
        has_reasoning_content=True,
        streaming_param="init",
    ),
    _ProviderInfo(
        name="anthropic",
        model_keywords=["claude"],
        url_keywords=["anthropic"],
        supports_tool_choice=True,
        has_reasoning_content=False,
        streaming_param="init",
    ),
    _ProviderInfo(
        name="openai",
        model_keywords=["gpt-", "o1", "o3", "o4", "openai"],
        url_keywords=["openai"],
        supports_tool_choice=True,
        has_reasoning_content=False,
        streaming_param="init",
    ),
]


def _detect_provider(settings: AgentSettings) -> _ProviderInfo:
    """根据 model 名称和 base_url 自动检测 provider。"""
    model = settings.model or ""
    base_url = settings.base_url or ""
    for p in _PROVIDERS:
        if p.matches(model, base_url):
            return p
    # 默认：OpenAI 兼容模式
    return _ProviderInfo(
        name="openai_compatible",
        model_keywords=[],
        url_keywords=[],
        supports_tool_choice=True,
        has_reasoning_content=False,
        streaming_param="stream",
    )


def _create_chat_model(
    settings: AgentSettings, provider: _ProviderInfo
) -> BaseChatModel:
    """根据 provider 创建对应的原生 ChatModel 实例。"""

    model_name = settings.model
    api_key = settings.api_key
    base_url = settings.base_url
    temperature = settings.temperature

    if provider.name == "deepseek":
        from langchain_deepseek import ChatDeepSeek

        class _FixedChatDeepSeek(ChatDeepSeek):
            """修复 reasoning_content 多轮回传的子类。

            ChatDeepSeek 在接收响应时将 reasoning_content 存入 additional_kwargs，
            但 _get_request_payload 构建下一轮请求时没有将其放回消息顶层，
            导致 API 报错 "reasoning_content must be passed back"。
            """

            def _get_request_payload(
                self,
                input_: LanguageModelInput,
                *,
                stop: list[str] | None = None,
                **kwargs: Any,
            ) -> dict:
                payload = super()._get_request_payload(input_, stop=stop, **kwargs)
                msgs = list(input_) if not isinstance(input_, list) else input_
                for i, msg in enumerate(payload["messages"]):
                    if msg.get("role") == "assistant" and i < len(msgs):
                        rc = msgs[i].additional_kwargs.get("reasoning_content")
                        if rc:
                            msg["reasoning_content"] = rc
                return payload

        return _FixedChatDeepSeek(
            model=model_name,
            api_key=api_key,
            api_base=base_url,
            temperature=temperature,
            streaming=settings.streaming,
        )

    if provider.name == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model_name=model_name,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
            streaming=settings.streaming,
        )

    if provider.name == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
        )

    # openai_compatible: 通用 OpenAI 兼容模式
    from langchain.chat_models import init_chat_model

    kwargs: dict[str, Any] = {
        "model": model_name,
        "api_key": api_key,
        "temperature": temperature,
        "base_url": base_url,
        "model_provider": "openai",
    }
    if settings.streaming:
        kwargs["streaming"] = True
    try:
        return init_chat_model(**kwargs)
    except TypeError:
        kwargs.pop("streaming", None)
        return init_chat_model(**kwargs)


# =========================
# 模型能力检测
# =========================

# 已知不支持 tool_choice 的模型（与 provider 检测互补）
_EXTRA_NO_TOOL_CHOICE_KEYWORDS = ["reasoner", "deepseek-r1", "o1-mini"]


def _model_supports_tool_choice(settings: AgentSettings, provider: _ProviderInfo) -> bool:
    """判断模型是否支持 tool_choice 参数。"""
    if not provider.supports_tool_choice:
        return False
    model_lower = (settings.model or "").lower()
    if any(kw in model_lower for kw in _EXTRA_NO_TOOL_CHOICE_KEYWORDS):
        return False
    return True


def _should_use_structured_output(settings: AgentSettings, provider: _ProviderInfo) -> bool:
    """综合用户设置和模型能力，决定是否启用结构化输出。"""
    if not settings.use_structured_output:
        return False
    if not _model_supports_tool_choice(settings, provider):
        return False
    return True


def detect_capabilities(settings: AgentSettings) -> dict:
    """公开 API：检测模型的各项能力，供前端/API 层使用。"""
    provider = _detect_provider(settings)
    return {
        "provider": provider.name,
        "structured_output": _should_use_structured_output(settings, provider),
        "reasoning_content": provider.has_reasoning_content,
        "streaming": settings.streaming,
    }


# =========================
# Agent 工厂
# =========================


def _init_model(settings: AgentSettings, callbacks: Optional[list[Any]] = None) -> BaseChatModel:
    """根据设置初始化底层 ChatModel（自动检测 provider）。"""

    missing: list[str] = []
    if not settings.api_key:
        missing.append("api_key")
    if not settings.base_url:
        missing.append("base_url")
    if not settings.model:
        missing.append("model")
    if missing:
        raise ValueError(
            "缺少必要字段：" + ", ".join(missing) + "。请在设置里填写。"
        )

    provider = _detect_provider(settings)
    logger = logging.getLogger(__name__)
    logger.info("detected provider=%s model=%s", provider.name, settings.model)

    model = _create_chat_model(settings, provider)

    if callbacks:
        try:
            model = model.with_config({"callbacks": callbacks})
        except Exception:
            pass

    return model


def _build_tools(settings: AgentSettings) -> list[Callable[..., Any]]:
    """根据工具开关组装工具列表。"""
    tools: list[Callable[..., Any]] = []
    if settings.tools_run_command:
        tools.append(run_command)
    if settings.tools_list_directory:
        tools.append(list_directory)
    if settings.tools_read_file:
        tools.append(read_file)
    if settings.tools_search_in_files:
        tools.append(search_in_files)
    if settings.tools_search_papers:
        tools.append(search_papers)
    if settings.tools_get_paper_details:
        tools.append(get_paper_details)
    if settings.tools_get_paper_citations:
        tools.append(get_paper_citations)
    if settings.tools_get_paper_references:
        tools.append(get_paper_references)
    if settings.tools_read_pdf:
        tools.append(read_pdf)
    return tools


def create_file_agent(
    thread_id: str = "file-agent-default",
    settings: Optional[AgentSettings] = None,
    callbacks: Optional[list[Any]] = None,
    db_path: str = ":memory:",
) -> tuple[Any, dict]:
    """创建 LangChain Agent 及其配置。

    参数:
        thread_id: 会话线程 ID，相同 ID 复用记忆。
        settings: AgentSettings，必须显式提供。
        callbacks: 运行时回调。
        db_path: SQLite 数据库路径。
    返回:
        (agent, config) 元组。
    """
    if settings is None:
        raise ValueError("create_file_agent 需要显式传入 settings。")

    conn = sqlite3.connect(db_path, check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    provider = _detect_provider(settings)
    model = _init_model(settings, callbacks=callbacks)
    tools = _build_tools(settings)

    use_so = _should_use_structured_output(settings, provider)
    logging.getLogger(__name__).info(
        "creating agent: provider=%s model=%s structured_output=%s",
        provider.name,
        settings.model,
        use_so,
    )

    agent = create_agent(
        model=model,
        system_prompt=settings.system_prompt,
        tools=tools,
        response_format=ResponseFormat if use_so else None,
        checkpointer=checkpointer,
    )

    config: dict[str, Any] = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 200,
    }
    if callbacks:
        config["callbacks"] = callbacks
    return agent, config


# =========================
# 会话查询辅助
# =========================


def list_threads(db_path: str) -> list[str]:
    try:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        cur = conn.execute(
            "SELECT DISTINCT thread_id FROM checkpoints ORDER BY checkpoint_id DESC"
        )
        return [row[0] for row in cur.fetchall()]
    except Exception:
        return []


def delete_thread(db_path: str, thread_id: str) -> None:
    try:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute("DELETE FROM checkpoints WHERE thread_id=?", (thread_id,))
        conn.execute("DELETE FROM writes WHERE thread_id=?", (thread_id,))
        conn.commit()
    except sqlite3.OperationalError:
        pass


def _extract_ai_answers(msgs: list[Any], cp: Any, conn: Any) -> list[str]:
    """从消息列表中提取 AI 回答（优先结构化输出，回退到 AIMessage 内容）。"""
    ai_msgs = [m for m in msgs if isinstance(m, AIMessage)]

    sr_rows = conn.execute(
        'SELECT type, value FROM writes WHERE channel="structured_response" ORDER BY checkpoint_id'
    ).fetchall()

    if sr_rows:
        answers = []
        for row in sr_rows:
            try:
                obj = cp.serde.loads_typed((row[0], row[1]))
                a = getattr(obj, "answer", None)
                if a:
                    answers.append(str(a))
            except Exception:
                pass
        if answers:
            return answers

    answers = []
    for msg in ai_msgs:
        content = getattr(msg, "content", "")
        if isinstance(content, str) and content.strip():
            answers.append(content)
    return answers


def get_thread_messages(db_path: str, thread_id: str) -> list[dict]:
    try:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        cp = SqliteSaver(conn)
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        tup = cp.get_tuple(config)
        if tup is None:
            return []

        msgs = tup.checkpoint.get("channel_values", {}).get("messages", [])
        human_msgs = [m for m in msgs if getattr(m, "type", "") == "human"]
        answers = _extract_ai_answers(msgs, cp, conn)

        result = []
        for i, hm in enumerate(human_msgs):
            content = getattr(hm, "content", "")
            if isinstance(content, list):
                content = " ".join(
                    c.get("text", "") if isinstance(c, dict) else str(c)
                    for c in content
                )
            result.append({"role": "user", "content": str(content)})
            if i < len(answers):
                result.append({"role": "assistant", "content": answers[i]})
        return result
    except Exception:
        logging.getLogger(__name__).exception(
            "get_thread_messages failed for %s", thread_id
        )
        return []


if __name__ == "__main__":
    raise SystemExit(
        "Please run `python api_server.py` and fill in API settings."
    )
