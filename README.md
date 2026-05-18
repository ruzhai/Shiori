# Shiori（栞）

基于 LangChain / LangGraph Agent 的本地文件智能助手桌面应用。通过自然语言对话，让 AI 帮助浏览、读取和搜索本地文件。

## 功能

- **目录浏览**：列出指定目录的文件和子目录结构
- **文件阅读**：读取文本文件内容，支持分片读取长文件
- **全文搜索**：递归搜索目录中包含指定关键字的文件
- **多 LLM 后端**：兼容 OpenAI / DeepSeek / Anthropic 等 OpenAI 接口规范的模型服务
- **会话管理**：新建、切换、删除、清空历史会话，基于 SQLite 持久化对话记忆
- **流式输出**：逐 token 渲染 LLM 回复，支持随时中断
- **运行日志**：实时展示工具调用轨迹与耗时，便于验证 Agent 行为
- **标题自动生成**：首条消息自动生成对话标题

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

1. 启动后点击 **设置（⚙）**，填写 API Key、Base URL、Model
2. 在输入框中用自然语言描述需求，例如：
   - `列出 D:/projects 目录`
   - `读取 D:/docs/readme.txt`
   - `在 D:/src 下搜索含 TODO 的文件`
3. 左侧 **会话面板** 管理历史对话，右侧 **运行流程面板** 查看工具调用日志

## 项目结构

```
Shiori/
├── main.py                    # Agent 工厂、模型初始化、会话 CRUD
├── tools.py                   # Agent 工具：list_directory / read_file / search_in_files
├── api_server.py              # Flask SSE 服务端（供 Electron 前端调用）
├── api_server_simple.py       # Flask 最小测试桩
├── start.bat                  # 一键启动脚本
├── electron-frontend/
│   ├── main.js                # Electron 主进程
│   ├── renderer.js            # React 渲染层
│   ├── index.html             # 入口页面
│   └── vendor/marked.min.js   # Markdown 解析
└── requirements.txt
```

## 架构

```
┌──────────────────────────────────────────┐
│  Electron + React 前端                     │
│  (electron-frontend/)                     │
└──────────────┬───────────────────────────┘
               │ HTTP + SSE
               ▼
┌──────────────────────────────────────────┐
│  Flask API 服务 (api_server.py)           │
│  - 会话管理 / 流式响应 / 标题生成          │
└──────────────┬───────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────┐
│  Agent 工厂 (main.py)                     │
│  - 模型初始化 (OpenAI 兼容协议)            │
│  - 工具组装 (list / read / search)        │
│  - LangGraph + SQLite checkpoint          │
└──────────────┬───────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────┐
│  工具层 (tools.py)                        │
│  list_directory / read_file / search      │
└──────────────────────────────────────────┘
```

## 技术栈

| 层级 | 技术 |
|---|---|
| Agent 框架 | LangChain / LangGraph |
| 模型接入 | langchain-openai / langchain-anthropic / langchain-deepseek |
| 会话持久化 | SQLite（langgraph-checkpoint-sqlite） |
| 桌面壳 | Electron |
| 前端 UI | React + marked |
| API 服务 | Flask + SSE |
| 运行环境 | Python 3.10+, Node.js |

## 许可

MIT
