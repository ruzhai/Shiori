# Shiori（栞）

基于 LangChain / LangGraph Agent 的多功能智能助手桌面应用。兼具**学术文献检索分析**和**本地文件管理**能力，通过自然语言对话完成任务。

## 功能

### 学术科研
- **论文搜索**：通过 Semantic Scholar API 搜索学术论文（支持 API Key 提速）
- **论文详情**：获取论文完整摘要、作者、引用数、发表信息
- **引用追踪**：查看论文的引用关系（谁引用了这篇 / 这篇引用了谁）
- **PDF 阅读**：支持本地 PDF 和 URL 自动下载，按需分页提取文本
- **文献综述**：自动搜索、筛选、整合多篇论文信息生成结构化综述

### 文件操作
- **目录浏览**：列出指定目录的文件和子目录结构
- **文件阅读**：读取文本文件内容，支持分片读取长文件
- **全文搜索**：递归搜索目录中包含指定关键字的文件
- **命令执行**：在 Windows 命令行中执行命令（需用户确认）

### 系统特性
- **多 LLM 后端**：自动检测 DeepSeek / OpenAI / Anthropic / OpenAI 兼容协议
- **流式输出**：SSE 实时推送回复内容，支持思考过程展示（DeepSeek Reasoner）
- **会话管理**：新建、切换、删除会话，SQLite 持久化对话记忆和历史
- **运行日志**：实时展示工具调用轨迹，便于验证 Agent 行为
- **命令确认**：危险操作弹出确认框，支持一键白名单
- **工具开关**：设置面板可单独启停每个工具

## 快速开始

```bash
# 1. 创建虚拟环境
python -m venv .venv
source .venv/bin/activate      # Linux / macOS
# .venv\Scripts\activate       # Windows

# 2. 安装 Python 依赖
pip install -r requirements.txt
pip install flask flask-cors

# 3. 安装 Node.js 依赖
cd electron-frontend && npm install && cd ..

# 4. 启动
start.bat                      # Windows
```

## 使用说明

1. 启动后点击 **设置（⚙）**，填写：
   - **API Key / Base URL / Model**：LLM 连接参数（必填）
   - **Semantic Scholar API Key**：可选，填写后可获得 1 rps 的搜索速率
   - **结构化输出**：Reasoner 类模型建议关闭
   - **工具开关**：按需启停各工具
2. 在输入框用自然语言描述需求，例如：
   - `帮我形成一篇 RAG 的文献综述`
   - `搜索 transformer attention mechanism 相关论文`
   - `列出 D:/projects 目录`
   - `读取 D:/docs/readme.txt 的内容`
3. 左侧 **会话面板** 管理历史对话，右侧 **运行流程面板** 查看工具调用日志

## 项目结构

```
Shiori/
├── main.py                    # Agent 工厂、模型初始化、Provider 检测、会话 CRUD
├── tools.py                   # 9 个 Agent 工具 + 确认上下文 + 安全计数器
├── semantic_scholar.py        # Semantic Scholar API 封装（缓存 + 限流 + 冷却）
├── api_server.py              # Flask SSE 服务端（供 Electron 前端调用）
├── start.bat                  # 一键启动脚本
├── electron-frontend/
│   ├── main.js                # Electron 主进程
│   ├── renderer.js            # React 渲染层（createElement）
│   ├── index.html             # 入口页面
│   └── vendor/marked.min.js   # Markdown 解析
├── scholar_cache.db           # Semantic Scholar 请求缓存（SQLite，1h TTL）
├── electron_agent.db          # Agent 对话记忆（LangGraph SQLite checkpoint）
└── requirements.txt
```

## 架构

```
┌──────────────────────────────────────────────────┐
│  Electron + React 前端                             │
│  renderer.js — 设置 / 会话 / 聊天 / 日志 / 确认     │
└──────────────────┬───────────────────────────────┘
                   │ HTTP POST + SSE streaming
                   ▼
┌──────────────────────────────────────────────────┐
│  Flask API 服务 (api_server.py)                   │
│  /api/init  → 初始化 Agent + 注入设置              │
│  /api/chat  → SSE 流式对话 + 命令确认桥接           │
│  /api/confirm → 用户确认/拒绝命令                  │
│  /api/threads → 会话 CRUD                        │
└──────────────────┬───────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────┐
│  Agent 工厂 (main.py)                             │
│  - Provider 自动检测 (DeepSeek/OpenAI/Anthropic)    │
│  - DeepSeek reasoning_content 回传修复             │
│  - 结构化输出 (ResponseFormat) 可选                │
│  - 工具组装 + LangGraph create_agent              │
│  - SQLite checkpoint 持久化                       │
└──────────────────┬───────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────┐
│  工具层 (tools.py)                                │
│  学术：search_papers / get_paper_details           │
│        get_paper_citations / get_paper_references  │
│        read_pdf (含 URL 下载)                      │
│  文件：list_directory / read_file / search_in_files│
│  系统：run_command (含 ConfirmContext 确认)         │
│                                                    │
│  安全机制：                                         │
│  - scholar 总调用上限 15 次（防无限搜索）            │
│  - scholar 连续错误 2 次 → 强制停止                  │
│  - read_pdf 上限 3 次/对话                          │
│  - API 429 → 30s 全局冷却                          │
│  - recursion_limit = 200                          │
└────────────────────┬──────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────┐
│  Semantic Scholar 连接器 (semantic_scholar.py)     │
│  - SQLite 缓存 (1h TTL，MD5 键)                   │
│  - 速率控制：有 Key 1s / 无 Key 2s 最小间隔         │
│  - 429 → 30s 全局冷却，不发出 HTTP 请求             │
│  - 最多重试 2 次，网络异常指数退避                   │
└──────────────────────────────────────────────────┘
```

## Provider 自动检测

系统根据 `model` 名称和 `base_url` 自动识别模型提供商，无需手动选择：

| Provider | 检测关键词 | 特殊处理 |
|---|---|---|
| DeepSeek | `deepseek`, `deepseek.com` | reasoning_content 回传修复、结构化输出 |
| Anthropic | `claude`, `anthropic` | 原生 ChatAnthropic |
| OpenAI | `gpt-`, `o1-o4`, `openai` | 原生 ChatOpenAI |
| OpenAI 兼容 | 默认回退 | `init_chat_model(provider="openai")` |

## 安全机制（防 Agent 无限循环）

| 层级 | 限制 | 触发后行为 |
|---|---|---|
| Scholar 总调用 | 15 次/消息 | 返回强制停止消息，Agent 无法继续搜索 |
| Scholar 连续错误 | 2 次 | 返回强制撰写指令 |
| PDF 调用 | 3 次/消息 | 工具直接拒绝 |
| API 429 冷却 | 30s 全局 | 所有实例拒绝发出 HTTP 请求 |
| Graph 递归 | 200 步 | LangGraph 截断 + 自动清理脏状态 |

所有计数器在每次用户发送新消息时自动归零。

## 工具清单

| 工具 | 功能 | 默认 |
|---|---|---|
| `search_papers` | Semantic Scholar 论文搜索 | ✅ |
| `get_paper_details` | 获取论文详细信息 | ✅ |
| `get_paper_citations` | 查询论文引用 | ✅ |
| `get_paper_references` | 查询参考文献 | ✅ |
| `read_pdf` | 读取 PDF（本地/URL） | ✅ |
| `list_directory` | 列出目录内容 | ✅ |
| `read_file` | 读取文本文件 | ✅ |
| `search_in_files` | 搜索文件内容 | ✅ |
| `run_command` | 执行 Windows 命令 | ✅ |

所有工具可在设置面板单独启停。

## 技术栈

| 层级 | 技术 |
|---|---|
| Agent 框架 | LangChain / LangGraph |
| 模型接入 | langchain-openai / langchain-anthropic / langchain-deepseek |
| 会话持久化 | SQLite（langgraph-checkpoint-sqlite） |
| 学术 API | Semantic Scholar Graph API + SQLite 缓存 |
| PDF 解析 | PyMuPDF (fitz) |
| 桌面壳 | Electron |
| 前端 UI | React (createElement) + marked |
| API 服务 | Flask + SSE + CORS |
| 运行环境 | Python 3.10+, Node.js |

## 许可

MIT
