from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import json
import queue
import sys
import os
import socket
import threading
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import create_file_agent, AgentSettings, SYSTEM_PROMPT, detect_capabilities
from main import list_threads, delete_thread, get_thread_messages
from tools import set_confirm_context, clear_confirm_context, ConfirmContext, set_scholar_api_key, reset_pdf_counter, reset_scholar_errors, reset_scholar_counter
from langgraph.errors import GraphRecursionError

STRICT_SYSTEM_PROMPT = SYSTEM_PROMPT  # 复用 main.py 中的统一定义


def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            s.close()
            return False
        except OSError:
            return True


app = Flask(__name__)
CORS(app)

agent = None
config = None
stop_event = threading.Event()
saved_settings = None
_use_structured_output = True
_active_confirm_ctx: ConfirmContext | None = None


def _db_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "electron_agent.db")


@app.route("/api/stop", methods=["POST"])
def stop():
    global _active_confirm_ctx
    stop_event.set()
    ctx = _active_confirm_ctx
    if ctx:
        ctx.reject_all_pending()
    return jsonify({"status": "ok"})


@app.route("/api/confirm", methods=["POST"])
def confirm():
    global _active_confirm_ctx
    data = request.json or {}
    req_id = data.get("id")
    approved = data.get("approved", False)
    add_whitelist = data.get("add_whitelist")
    ctx = _active_confirm_ctx
    if ctx is None:
        return jsonify({"status": "error", "message": "没有待确认的命令"}), 400
    if add_whitelist:
        ctx.add_whitelist(add_whitelist)
    ctx.resolve(req_id, approved)
    return jsonify({"status": "ok"})


@app.route("/api/test", methods=["GET"])
def test():
    return jsonify({"status": "ok"})


@app.route("/api/generate_title", methods=["POST"])
def generate_title():
    global saved_settings
    if not saved_settings:
        return jsonify({"title": "新对话"})
    message = request.json.get("message", "")
    if not message:
        return jsonify({"title": "新对话"})
    try:
        from langchain.chat_models import init_chat_model

        title_model = init_chat_model(
            model=saved_settings.get("model", ""),
            api_key=saved_settings.get("api_key", ""),
            base_url=saved_settings.get("base_url", ""),
            temperature=0,
            model_provider="openai",
        )
        resp = title_model.invoke(
            [
                {
                    "role": "system",
                    "content": "你是一个标题生成器。根据用户的第一条消息，生成一个简短的对话标题（2-8个汉字）。只回复标题本身，不要加引号、句号或任何解释。",
                },
                {"role": "user", "content": f"为以下对话起个简短标题：\n\n{message}"},
            ]
        )
        title = resp.content.strip()[:20]
        return jsonify({"title": title or "新对话"})
    except Exception:
        return jsonify({"title": "新对话"})


@app.route("/api/init", methods=["POST"])
def init_agent():
    global agent, config, saved_settings, _use_structured_output
    data = request.json
    saved_settings = data

    model_name = data.get("model", "")
    base_url = data.get("base_url", "")
    # 前端可传 use_structured_output，否则自动检测
    user_choice = data.get("use_structured_output")
    if user_choice is not None:
        _use_structured_output = bool(user_choice)
    else:
        caps = detect_capabilities(
            AgentSettings(
                model=model_name,
                base_url=base_url,
                api_key=data.get("api_key", ""),
            )
        )
        _use_structured_output = caps["structured_output"]

    try:
        scholar_key = data.get("scholar_api_key", "")
        set_scholar_api_key(scholar_key or None)

        settings = AgentSettings(
            api_key=data.get("api_key"),
            base_url=base_url,
            model=model_name,
            temperature=float(data.get("temperature", 0.0)),
            system_prompt=STRICT_SYSTEM_PROMPT,
            streaming=True,
            scholar_api_key=scholar_key or None,
            tools_run_command=data.get("tools_run_command", True),
            tools_list_directory=data.get("tools_list_directory", True),
            tools_read_file=data.get("tools_read_file", True),
            tools_search_in_files=data.get("tools_search_in_files", True),
            tools_search_papers=data.get("tools_search_papers", True),
            tools_get_paper_details=data.get("tools_get_paper_details", True),
            tools_get_paper_citations=data.get("tools_get_paper_citations", True),
            tools_get_paper_references=data.get("tools_get_paper_references", True),
            tools_read_pdf=data.get("tools_read_pdf", True),
            use_structured_output=_use_structured_output,
        )
        agent, config = create_file_agent(
            thread_id="electron-session", settings=settings, db_path="electron_agent.db"
        )
        return jsonify(
            {
                "status": "ok",
                "use_structured_output": _use_structured_output,
            }
        )
    except Exception as e:
        import traceback

        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


def _extract_answer_from_chunk(node_data: dict) -> str | None:
    """从 stream chunk 中提取 AI 回复文本（无结构化输出时使用）。"""
    if "messages" not in node_data:
        return None
    for msg in reversed(node_data["messages"]):
        # 跳过 ToolMessage
        if hasattr(msg, "tool_call_id"):
            continue
        content = getattr(msg, "content", "")
        if isinstance(content, str) and content.strip():
            return content
    return None


def _extract_reasoning(node_data: dict) -> str | None:
    """从 stream chunk 中提取思考过程（DeepSeek reasoning_content）。"""
    if "messages" not in node_data:
        return None
    for msg in reversed(node_data["messages"]):
        if hasattr(msg, "tool_call_id"):
            continue
        rc = getattr(msg, "additional_kwargs", {}).get("reasoning_content")
        if rc and isinstance(rc, str) and rc.strip():
            return rc
    return None


@app.route("/api/chat", methods=["POST"])
def chat():
    global agent, config, _use_structured_output, _active_confirm_ctx
    if not agent:
        return jsonify({"error": "Agent not initialized"}), 400
    data = request.json
    message = data.get("message", "")
    thread_id = data.get("thread_id", "electron-session")
    whitelist = set(data.get("whitelist", []))

    def _process_chunk(chunk: dict, acc: dict) -> list[str]:
        """处理单个 stream chunk，返回 SSE 事件字符串列表。

        acc 是可变累加器，包含 "content" 和 "reasoning" 两个键，
        用于跨 chunk 去重，避免多轮思考时重复输出相同句子。
        """
        events = []
        for node_name, node_data in chunk.items():
            if node_name == "tools" and "messages" in node_data:
                for msg in node_data["messages"]:
                    output = str(getattr(msg, "content", ""))
                    first_line = output.strip().split("\n")[0][:60]
                    events.append(json.dumps({
                        "type": "tool_end",
                        "tool": first_line or getattr(msg, "name", ""),
                        "output": output[:300],
                    }, ensure_ascii=False))
            else:
                if "messages" in node_data:
                    for msg in node_data["messages"]:
                        for tc in getattr(msg, "tool_calls", []):
                            args = tc.get("args", {})
                            if isinstance(args, dict):
                                cmd = args.get("command", "")[:60]
                            else:
                                cmd = str(args)[:60]
                            events.append(json.dumps({
                                "type": "tool_start",
                                "tool": cmd or tc.get("name", ""),
                                "input": str(args)[:200],
                            }, ensure_ascii=False))

                if "structured_response" in node_data:
                    sr = node_data["structured_response"]
                    answer = getattr(sr, "answer", str(sr))
                    if answer != acc["content"]:
                        acc["content"] = answer
                        events.append(json.dumps({
                            "type": "content", "data": answer
                        }, ensure_ascii=False))
                elif not _use_structured_output:
                    reasoning = _extract_reasoning(node_data)
                    if reasoning and reasoning != acc["reasoning"]:
                        acc["reasoning"] = reasoning
                        events.append(json.dumps({
                            "type": "reasoning", "data": reasoning
                        }, ensure_ascii=False))
                    answer = _extract_answer_from_chunk(node_data)
                    if answer and answer != acc["content"]:
                        acc["content"] = answer
                        events.append(json.dumps({
                            "type": "content", "data": answer
                        }, ensure_ascii=False))
        return events

    def generate():
        global _active_confirm_ctx

        reset_pdf_counter()
        reset_scholar_errors()
        reset_scholar_counter()
        stop_event.clear()

        confirm_ctx = ConfirmContext(whitelist=whitelist, timeout=120.0)
        set_confirm_context(confirm_ctx)
        _active_confirm_ctx = confirm_ctx

        output_queue: queue.Queue = queue.Queue()
        agent_error: str | None = None
        agent_done = threading.Event()
        acc = {"content": "", "reasoning": ""}

        def run_agent():
            nonlocal agent_error
            try:
                for chunk in agent.stream(
                    {"messages": [{"role": "user", "content": message}]},
                    {**config, "configurable": {"thread_id": thread_id}, "recursion_limit": 200},
                    stream_mode="updates",
                ):
                    if stop_event.is_set():
                        break
                    output_queue.put(("chunk", chunk))
                output_queue.put(("done", None))
            except GraphRecursionError:
                delete_thread(_db_path(), thread_id)
                agent_error = "recursion_limit"
                output_queue.put(("recursion_limit", None))
            except Exception as e:
                agent_error = str(e)
                # 孤儿 tool_calls → 自动清理损坏的线程状态
                if "tool_calls" in agent_error or "insufficient tool messages" in agent_error:
                    delete_thread(_db_path(), thread_id)
                    output_queue.put(("reset", agent_error))
                else:
                    output_queue.put(("error", agent_error))
            finally:
                agent_done.set()

        agent_thread = threading.Thread(target=run_agent, daemon=True)
        agent_thread.start()

        try:
            while not (agent_done.is_set() and output_queue.empty()):
                # ① 检查确认队列
                confirm_req = confirm_ctx.poll_pending()
                if confirm_req:
                    req_id = confirm_req["id"]
                    yield f"data: {json.dumps({'type': 'confirm_command', 'id': req_id, 'command': confirm_req['command'], 'working_dir': confirm_req.get('working_dir', '')}, ensure_ascii=False)}\n\n"

                    # 阻塞等待用户确认
                    while not confirm_ctx.is_resolved(req_id):
                        if stop_event.is_set():
                            confirm_ctx.resolve(req_id, False)
                            yield f"data: {json.dumps({'type': 'error', 'data': '已停止'}, ensure_ascii=False)}\n\n"
                            return
                        time.sleep(0.1)
                    continue

                # ② 检查停止
                if stop_event.is_set():
                    yield f"data: {json.dumps({'type': 'error', 'data': '已停止'}, ensure_ascii=False)}\n\n"
                    return

                # ③ 取 Agent 输出
                try:
                    item_type, item_data = output_queue.get(timeout=0.2)
                except queue.Empty:
                    continue

                if item_type == "chunk":
                    for ev_str in _process_chunk(item_data, acc):
                        yield f"data: {ev_str}\n\n"

                elif item_type == "done":
                    yield "data: [DONE]\n\n"
                    return

                elif item_type == "recursion_limit":
                    yield f"data: {json.dumps({'type': 'error', 'data': '工具调用次数达到上限，对话已自动重置。请新建会话或重新发送消息。'}, ensure_ascii=False)}\n\n"
                    return

                elif item_type == "reset":
                    yield f"data: {json.dumps({'type': 'error', 'data': '检测到对话状态异常，已自动修复。请重新发送消息。'}, ensure_ascii=False)}\n\n"
                    return

                elif item_type == "error":
                    traceback.print_exc()
                    yield f"data: {json.dumps({'type': 'error', 'data': item_data}, ensure_ascii=False)}\n\n"
                    return

        finally:
            clear_confirm_context()
            _active_confirm_ctx = None
            stop_event.clear()
            confirm_ctx.reject_all_pending()

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/threads", methods=["GET"])
def list_threads_api():
    return jsonify({"threads": list_threads(_db_path())})


@app.route("/api/threads/<thread_id>", methods=["DELETE"])
def delete_thread_api(thread_id):
    delete_thread(_db_path(), thread_id)
    return jsonify({"status": "ok"})


@app.route("/api/threads/<thread_id>/messages", methods=["GET"])
def get_thread_messages_api(thread_id):
    return jsonify({"messages": get_thread_messages(_db_path(), thread_id)})


if __name__ == "__main__":
    if is_port_in_use(5000):
        print("Port 5000 is already in use. Exiting.")
        sys.exit(1)
    app.run(port=5000, debug=False, threaded=True)
