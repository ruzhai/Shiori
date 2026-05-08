from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import json
import sys
import os
import socket

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import create_file_agent, AgentSettings, SYSTEM_PROMPT
from main import list_threads, delete_thread, get_thread_messages

STRICT_SYSTEM_PROMPT = """你是一个本地文件助手，工作在 Windows 系统上。

## 回复决策流程（严格遵守）

收到用户消息后，按以下流程决定如何回复：

### 第1步：判断意图
用户是否明确要求了文件操作？
  - 文件操作仅包括：列出目录内容、读取文件、搜索文件内容
  - 明确要求的特征：用户指定了具体路径 + 操作动词（列出/读取/搜索/查看/找）

### 第2步：选择回复方式
  - 如果用户只是问候（如"你好"）、闲聊、问概念性问题 → 直接回复，不调用工具
  - 只有当用户明确说了路径和操作，才调用对应工具
  - 如果用户请求模糊（只说"帮我看看文件"没有路径）→ 先询问用户要操作哪个路径

### 第3步：工具使用限制
  - 每次只调用一个工具
  - 获得结果后立即向用户报告，不要连续调用多个工具

## 路径规则
- 路径由用户提供，禁止猜测或自行构造
- Windows 格式：D:\\xxx\\yyy

## 回答风格
- 使用简体中文，语气友好直接"""

def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(('127.0.0.1', port))
            s.close()
            return False
        except OSError:
            return True

app = Flask(__name__)
CORS(app)

agent = None
config = None
stop_flag = False
saved_settings = None

def _db_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'electron_agent.db')

@app.route('/api/stop', methods=['POST'])
def stop():
    global stop_flag
    stop_flag = True
    return jsonify({'status': 'ok'})

@app.route('/api/test', methods=['GET'])
def test():
    return jsonify({'status': 'ok'})

@app.route('/api/generate_title', methods=['POST'])
def generate_title():
    global saved_settings
    if not saved_settings:
        return jsonify({'title': '新对话'})
    message = request.json.get('message', '')
    if not message:
        return jsonify({'title': '新对话'})
    try:
        from langchain.chat_models import init_chat_model
        title_model = init_chat_model(
            model=saved_settings.get('model', ''),
            api_key=saved_settings.get('api_key', ''),
            base_url=saved_settings.get('base_url', ''),
            temperature=0,
            model_provider='openai',
        )
        resp = title_model.invoke([
            {'role': 'system', 'content': '你是一个标题生成器。根据用户的第一条消息，生成一个简短的对话标题（2-8个汉字）。只回复标题本身，不要加引号、句号或任何解释。'},
            {'role': 'user', 'content': f'为以下对话起个简短标题：\n\n{message}'}
        ])
        title = resp.content.strip()[:20]
        return jsonify({'title': title or '新对话'})
    except Exception:
        return jsonify({'title': '新对话'})

@app.route('/api/init', methods=['POST'])
def init_agent():
    global agent, config, saved_settings
    data = request.json
    saved_settings = data
    try:
        settings = AgentSettings(
            api_key=data.get('api_key'),
            base_url=data.get('base_url'),
            model=data.get('model'),
            temperature=float(data.get('temperature', 0.0)),
            system_prompt=STRICT_SYSTEM_PROMPT,
            streaming=True,
            tools_list_directory=True,
            tools_read_file=True,
            tools_search_in_files=True
        )
        agent, config = create_file_agent(
            thread_id='electron-session',
            settings=settings,
            db_path='electron_agent.db'
        )
        return jsonify({'status': 'ok'})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/chat', methods=['POST'])
def chat():
    global agent, config
    if not agent:
        return jsonify({'error': 'Agent not initialized'}), 400
    data = request.json
    message = data.get('message', '')
    thread_id = data.get('thread_id', 'electron-session')

    def generate():
        global stop_flag
        stop_flag = False
        max_retries = 3
        last_error = None
        for attempt in range(max_retries):
            if stop_flag:
                return
            try:
                tool_call_count = 0
                for chunk in agent.stream(
                    {"messages": [{"role": "user", "content": message}]},
                    {**config, "configurable": {"thread_id": thread_id}, "recursion_limit": 25},
                    stream_mode='updates'
                ):
                    if stop_flag:
                        yield f"data: {json.dumps({'type': 'error', 'data': '已停止'})}\n\n"
                        return
                    for node_name, node_data in chunk.items():
                        if node_name == 'tools' and 'messages' in node_data:
                            tool_call_count += 1
                            if tool_call_count > 10:
                                yield f"data: {json.dumps({'type': 'error', 'data': '工具调用次数超过限制，已停止'})}\n\n"
                                return
                            for msg in node_data['messages']:
                                event = {'type': 'tool_end', 'tool': getattr(msg, 'name', ''), 'output': str(getattr(msg, 'content', ''))[:300]}
                                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                        elif 'messages' in node_data:
                            for msg in node_data['messages']:
                                for tc in getattr(msg, 'tool_calls', []):
                                    event = {'type': 'tool_start', 'tool': tc.get('name',''), 'input': str(tc.get('args',''))[:200]}
                                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                        if 'structured_response' in node_data:
                            sr = node_data['structured_response']
                            answer = getattr(sr, 'answer', str(sr))
                            yield f"data: {json.dumps({'type': 'content', 'data': answer}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
                return
            except Exception as e:
                last_error = e
                if '503' in str(e) or 'service_unavailable' in str(e) or 'busy' in str(e):
                    if attempt < max_retries - 1:
                        import time
                        time.sleep(1.5 * (attempt + 1))
                        continue
                break
        import traceback; traceback.print_exc()
        yield f"data: {json.dumps({'type': 'error', 'data': str(last_error)}, ensure_ascii=False)}\n\n"

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

@app.route('/api/threads', methods=['GET'])
def list_threads_api():
    return jsonify({'threads': list_threads(_db_path())})

@app.route('/api/threads/<thread_id>', methods=['DELETE'])
def delete_thread_api(thread_id):
    delete_thread(_db_path(), thread_id)
    return jsonify({'status': 'ok'})

@app.route('/api/threads/<thread_id>/messages', methods=['GET'])
def get_thread_messages_api(thread_id):
    return jsonify({'messages': get_thread_messages(_db_path(), thread_id)})

if __name__ == '__main__':
    if is_port_in_use(5000):
        print('Port 5000 is already in use. Exiting.')
        sys.exit(1)
    app.run(port=5000, debug=False, threaded=True)
