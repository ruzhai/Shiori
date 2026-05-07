from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.sqlite import SqliteSaver

from tools import list_directory, read_file, search_in_files
# =========================
# 系统提示 & 响应结构
# =========================
#
# 这一段主要定义：
# 1. Agent 的“身份”和行为规范（SYSTEM_PROMPT）
# 2. Agent 输出时的结构化数据格式（ResponseFormat）
#
# LangChain 会将 SYSTEM_PROMPT 当成系统级提示词，
# 决定 Agent 在调用工具时的策略和回答风格。

SYSTEM_PROMPT = """你是一个本地文件助手。

规则：
1. 只有当用户明确要求操作文件或目录时，才调用工具
2. 普通对话（问候、问答等）直接回复，不调用任何工具
3. 路径必须由用户提供，不要自行猜测或遍历
4. 每次只调用一个工具，获得结果后立即回复用户
5. 系统是 Windows，路径格式为 D:\\xxx，不要使用 / 开头的路径

回答使用简体中文。"""


@dataclass
class AgentSettings:
    """创建 Agent/模型所需的可配置项（用于 GUI 的“设置”页）。

    约定：
    - 本项目不再依赖环境变量/.env；所有模型连接参数必须由 settings 显式提供
    - tools_* 用于让用户在 GUI 中按需启用/禁用工具
    """

    # 模型连接参数（必须显式提供；不再从环境变量回退）
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    temperature: float = 0

    # 系统提示词（GUI 中允许用户自定义）
    system_prompt: str = SYSTEM_PROMPT

    # 工具开关（GUI 中允许用户自定义）
    tools_list_directory: bool = True
    tools_read_file: bool = True
    tools_search_in_files: bool = True

    # 尝试开启底层模型的流式输出（若后端/版本不支持会自动降级）
    streaming: bool = False


@dataclass
class ResponseFormat:
    """代理响应结构。

    使用 dataclass 作为 response_format 的好处：
    - Agent 在内部会始终返回这个结构，前端/CLI 可以统一取 structured_response.answer
    - 便于后续扩展字段，比如新增 tokens 消耗、危险操作确认结果等
    """

    # 给用户的自然语言回答
    answer: str
    # （可选）本轮中重点涉及的文件路径
    related_files: Optional[List[str]] = None


# =========================
# 工具定义
# =========================
#
# 工具函数已拆分到 `file_tools.py`，这里直接 import 使用，
# 便于后续扩展更多工具并保持 main.py 结构清晰。


# =========================
# 模型 & 代理工厂
# =========================
#
# 这里做两件事：
# 1. 根据环境变量初始化底层 LLM 对象（model）
# 2. 暴露 create_file_agent 工厂函数，供 GUI / CLI 复用

def _init_model(settings: AgentSettings, callbacks: Optional[list[Any]] = None) -> Any:
    """根据设置初始化底层 ChatModel。

    说明：
    - 为了兼容不同版本/不同 provider，这里对 streaming 参数做了“尽力而为”式传递：
      如果当前 init_chat_model 不支持 streaming，会自动捕获 TypeError 并降级。
    - callbacks 用于 GUI 侧做“流式 token 回调”和“工具调用日志”。
    """

    # 只依赖 settings，不再隐式回退环境变量（GUI/调用方应显式提供）
    api_key = settings.api_key
    base_url = settings.base_url
    llm_model = settings.model

    missing: list[str] = []
    if not api_key:
        missing.append("api_key")
    if not base_url:
        missing.append("base_url")
    if not llm_model:
        missing.append("model")
    if missing:
        raise ValueError(
            "AgentSettings 缺少必要字段："
            + ", ".join(missing)
            + "。请在 GUI 的“设置”里填写，或在代码中显式传入 AgentSettings。"
        )

    kwargs: dict[str, Any] = {
        "model": llm_model,
        "api_key": api_key,
        "temperature": settings.temperature,
        "base_url": base_url,
        "model_provider": "openai",  # 默认 openai 兼容
    }

    if settings.streaming:
        kwargs["streaming"] = True

    try:
        model = init_chat_model(**kwargs)
    except TypeError:
        kwargs.pop("streaming", None)
        model = init_chat_model(**kwargs)

    # 有些 LangChain 对象支持 with_config 注入 callbacks；不支持则忽略
    if callbacks:
        try:
            model = model.with_config({"callbacks": callbacks})
        except Exception:
            pass

    return model


def _build_tools(settings: AgentSettings) -> list[Callable[..., Any]]:
    """根据工具开关组装工具列表。"""

    tools: list[Callable[..., Any]] = []
    if settings.tools_list_directory:
        tools.append(list_directory)
    if settings.tools_read_file:
        tools.append(read_file)
    if settings.tools_search_in_files:
        tools.append(search_in_files)
    return tools


def create_file_agent(
    thread_id: str = "file-agent-default",
    settings: Optional[AgentSettings] = None,
    callbacks: Optional[list[Any]] = None,
    db_path: str = ":memory:",
) -> tuple[Any, dict]:
    """创建一个用于本地文件管理/分析的 LangChain 代理及其配置。

    参数:
        thread_id: 会话线程 ID。相同 ID 会复用记忆（同一轮对话），
                   不同 ID 则彼此独立（适合 GUI / CLI 各自使用）。
        settings: 可选的 AgentSettings。若不传入则会直接报错（因为不允许回退环境变量）。
        callbacks: 运行时回调（GUI 用于流式输出与工具日志）。
        db_path: SQLite 数据库路径，默认内存模式；传入文件路径则持久化。
    返回:
        (agent, config) 元组：
        - agent: 可直接调用 .invoke(...) 的 LangChain 代理
        - config: 内部使用的配置字典，主要包含 thread_id / checkpoint 设置
    """
    if settings is None:
        raise ValueError(
            "create_file_agent 需要显式传入 settings（本项目不再从 .env/环境变量读取配置）。"
        )

    import sqlite3
    conn = sqlite3.connect(db_path, check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    model = _init_model(settings, callbacks=callbacks)
    tools = _build_tools(settings)

    agent = create_agent(
        model=model,
        system_prompt=settings.system_prompt,
        tools=tools,
        response_format=ResponseFormat,
        checkpointer=checkpointer,
    )

    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    if callbacks:
        config["callbacks"] = callbacks
    return agent, config


def list_threads(db_path: str) -> list[str]:
    import sqlite3
    try:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        cur = conn.execute(
            'SELECT DISTINCT thread_id FROM checkpoints ORDER BY checkpoint_id DESC'
        )
        return [row[0] for row in cur.fetchall()]
    except Exception:
        return []


def delete_thread(db_path: str, thread_id: str) -> None:
    import sqlite3
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute('DELETE FROM checkpoints WHERE thread_id=?', (thread_id,))
    conn.execute('DELETE FROM writes WHERE thread_id=?', (thread_id,))
    conn.commit()


def get_thread_messages(db_path: str, thread_id: str) -> list[dict]:
    import sqlite3, logging
    try:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        cp = SqliteSaver(conn)
        config = {'configurable': {'thread_id': thread_id, 'checkpoint_ns': ''}}
        tup = cp.get_tuple(config)
        if tup is None:
            return []
        msgs = tup.checkpoint.get('channel_values', {}).get('messages', [])
        human_msgs = [m for m in msgs if getattr(m, 'type', '') == 'human']

        sr_rows = conn.execute(
            'SELECT type, value FROM writes WHERE thread_id=? AND channel="structured_response" ORDER BY checkpoint_id',
            (thread_id,),
        ).fetchall()
        answers = []
        for row in sr_rows:
            try:
                obj = cp.serde.loads_typed((row[0], row[1]))
                a = getattr(obj, 'answer', None)
                if a:
                    answers.append(str(a))
            except Exception:
                pass

        result = []
        for i, hm in enumerate(human_msgs):
            content = getattr(hm, 'content', '')
            if isinstance(content, list):
                content = ' '.join(c.get('text', '') if isinstance(c, dict) else str(c) for c in content)
            result.append({'role': 'user', 'content': str(content)})
            if i < len(answers):
                result.append({'role': 'assistant', 'content': answers[i]})
        return result
    except Exception:
        logging.getLogger(__name__).exception('get_thread_messages failed for %s', thread_id)
        return []


if __name__ == '__main__':
    raise SystemExit(
        'Please run `python gui.py` and fill in API settings.'
    )
