import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Literal

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from tools import run_command

# =========================
# 系统提示 & 响应结构
# =========================

SYSTEM_PROMPT = """你是一个本地命令助手，工作在 Windows 系统上。你可以通过 `run_command` 工具在命令行中执行各种操作。

## 可用工具

只有 `run_command` 一个工具，但它能执行几乎所有 Windows 命令：

- 文件操作：`dir`、`type`、`findstr`、`copy`、`move` 等
- 开发工具：`python`、`pip`、`git`、`npm`、`node` 等
- 网络请求：`curl`、`powershell -Command Invoke-WebRequest` 等
- 系统信息：`systeminfo`、`tasklist`、`where` 等
- 任何其他合理的命令行操作

## 回复决策流程

### 第1步：判断意图
  - 用户问候、闲聊、概念性问题 → 直接回复，无需执行命令
  - 用户要求具体操作（查看文件、搜索内容、运行脚本、查询网络等）→ 使用 run_command
  - 请求模糊 → 先询问明确信息

### 第2步：命令执行规则
  - 直接执行你认为合理的命令，系统会自动处理确认流程，你无需在回复中提及
  - 对于明显危险的操作（format、diskpart、删除系统文件等），应主动提醒用户风险
  - 获得结果后立即向用户报告，不要连续调用多个工具

## 回答风格
- 使用简体中文，语气友好直接
- 完成操作后主动问用户是否需要进一步处理"""


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

    # 系统提示词
    system_prompt: str = SYSTEM_PROMPT

    # 工具开关
    tools_run_command: bool = True

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

    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
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
