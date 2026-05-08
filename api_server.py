from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import json
import sys
import os
import socket

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import create_file_agent, AgentSettings, SYSTEM_PROMPT

STRICT_SYSTEM_PROMPT = """你是一个本地文件助手。

规则：
1. 只有当用户明确要求操作文件或目录时，才调用工具
2. 普通对话（问候、问答等）直接回复，不调用任何工具
3. 路径必须由用户提供，不要自行猜测或遍历
4. 每次只调用一个工具，获得结果后立即回复用户
5. 系统是 Windows，路径格式为 D:\\xxx，不要使用 / 开头的路径

回答使用简体中文。"""

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

@app.route('/api/stop', methods=['POST'])
def stop():
    global stop_flag
    stop_flag = True
    return jsonify({'status': 'ok'})

@app.route('/api/test', methods=['GET'])
def test():
    return jsonify({'status': 'ok'})

@app.route('/api/init', methods=['POST'])
def init_agent():
    global agent, config
    data = request.json
    try:
        # 删除旧数据库避免脏历史
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'electron_agent.db')
        for f in [db_path, db_path + '-shm', db_path + '-wal']:
            try:
                os.remove(f)
            except OSError:
                pass
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

    def generate():
        global stop_flag
        stop_flag = False
        try:
            tool_call_count = 0
            for chunk in agent.stream(
                {"messages": [{"role": "user", "content": message}]},
                {**config, "recursion_limit": 25},
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
        except Exception as e:
            import traceback; traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'data': str(e)})}\n\n"

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

if __name__ == '__main__':
    if is_port_in_use(5000):
        print('Port 5000 is already in use. Exiting.')
        sys.exit(1)
    app.run(port=5000, debug=False, threaded=True)
