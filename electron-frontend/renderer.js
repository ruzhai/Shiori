const React = require('react');
const ReactDOM = require('react-dom/client');
const { useState, useEffect } = React;

const BACKEND = 'http://localhost:5000';

// ── 样式常量 ──────────────────────────────────────────────
const S = {
  input: {
    width: '100%', padding: '10px 14px', background: '#2e2e2e',
    border: '1px solid #3e3e3e', borderRadius: '8px',
    color: '#e0e0e0', fontSize: '13px', marginBottom: '12px', display: 'block'
  },
  btn: (bg = '#3e3e3e') => ({
    flex: 1, padding: '8px', background: bg, color: '#fff',
    border: 'none', borderRadius: '6px', cursor: 'pointer', fontSize: '13px'
  }),
};

// ── SettingsModal ─────────────────────────────────────────
function SettingsModal({ onClose, onSave }) {
  const saved = JSON.parse(localStorage.getItem('shiori_settings') || '{}');
  const [apiKey, setApiKey] = useState(saved.api_key || '');
  const [baseUrl, setBaseUrl] = useState(saved.base_url || '');
  const [model, setModel] = useState(saved.model || '');

  const handleSave = () => {
    const settings = { api_key: apiKey, base_url: baseUrl, model: model };
    localStorage.setItem('shiori_settings', JSON.stringify(settings));
    onSave(settings);
    onClose();
  };

  return React.createElement('div', {
    style: {
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000
    }
  },
    React.createElement('div', { style: { background: '#2e2e2e', padding: '24px', borderRadius: '12px', width: '400px' } },
      React.createElement('h2', { style: { marginBottom: '16px', fontSize: '16px' } }, '设置'),
      React.createElement('input', { type: 'password', placeholder: 'API Key', value: apiKey, onChange: e => setApiKey(e.target.value), style: S.input }),
      React.createElement('input', { type: 'text', placeholder: 'Base URL (e.g. https://api.openai.com/v1)', value: baseUrl, onChange: e => setBaseUrl(e.target.value), style: S.input }),
      React.createElement('input', { type: 'text', placeholder: 'Model (e.g. gpt-4o)', value: model, onChange: e => setModel(e.target.value), style: S.input }),
      React.createElement('div', { style: { display: 'flex', gap: '8px' } },
        React.createElement('button', { onClick: handleSave, style: S.btn('#1971c2') }, '保存'),
        React.createElement('button', { onClick: onClose, style: S.btn() }, '取消')
      )
    )
  );
}

// ── SessionList ───────────────────────────────────────────
function SessionList({ sessions, current, onSelect, onNew, onSettings }) {
  return React.createElement('div', {
    style: { width: '220px', borderRight: '1px solid #3e3e3e', display: 'flex', flexDirection: 'column', background: '#1e1e1e' }
  },
    React.createElement('div', { style: { padding: '12px', borderBottom: '1px solid #3e3e3e', display: 'flex', gap: '8px' } },
      React.createElement('button', { onClick: onNew, style: { ...S.btn('#1971c2'), flex: 3 } }, '+ 新建'),
      React.createElement('button', { onClick: onSettings, style: { ...S.btn(), flex: 1, padding: '8px 6px' } }, '⚙')
    ),
    React.createElement('div', { style: { flex: 1, overflowY: 'auto', padding: '8px' } },
      sessions.map(s => React.createElement('div', {
        key: s.id,
        onClick: () => onSelect(s.id),
        style: {
          padding: '10px 12px', borderRadius: '6px', cursor: 'pointer', marginBottom: '4px',
          background: s.id === current ? '#2e2e2e' : 'transparent', fontSize: '13px'
        }
      }, s.title))
    )
  );
}

// ── LogPanel ──────────────────────────────────────────────
function LogPanel({ logs, onClear }) {
  const ref = React.useRef(null);
  useEffect(() => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; }, [logs]);

  return React.createElement('div', {
    style: { width: '280px', borderLeft: '1px solid #3e3e3e', display: 'flex', flexDirection: 'column', background: '#1e1e1e' }
  },
    React.createElement('div', {
      style: { padding: '10px 14px', borderBottom: '1px solid #3e3e3e', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }
    },
      React.createElement('span', { style: { fontSize: '12px', fontWeight: '600', color: '#a0a0a0' } }, '运行详情'),
      React.createElement('button', { onClick: onClear, style: { ...S.btn(), padding: '3px 8px', fontSize: '11px', flex: 'none' } }, '清空')
    ),
    React.createElement('div', {
      ref,
      style: { flex: 1, overflowY: 'auto', padding: '10px', fontSize: '11px', fontFamily: 'Consolas, monospace', color: '#888', lineHeight: '1.6' }
    },
      logs.length === 0
        ? React.createElement('div', { style: { color: '#555' } }, '等待运行...')
        : logs.map((log, i) => React.createElement('div', { key: i, style: { marginBottom: '6px', whiteSpace: 'pre-wrap', borderBottom: '1px solid #2a2a2a', paddingBottom: '6px' } }, log))
    )
  );
}

// ── ChatArea ──────────────────────────────────────────────
function ChatArea({ messages, input, setInput, onSend, onStop, agentReady, running }) {
  const bottomRef = React.useRef(null);
  useEffect(() => { if (bottomRef.current) bottomRef.current.scrollIntoView({ behavior: 'smooth' }); }, [messages]);

  return React.createElement('div', { style: { flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 } },
    // 状态栏
    React.createElement('div', {
      style: { padding: '6px 16px', borderBottom: '1px solid #3e3e3e', fontSize: '11px', color: agentReady ? '#4caf50' : '#ff9800' }
    }, agentReady ? '● Agent 就绪' : '● 未初始化 — 请点击设置配置 API'),
    // 消息列表
    React.createElement('div', { style: { flex: 1, overflowY: 'auto', padding: '16px' } },
      messages.map((msg, i) => React.createElement('div', {
        key: i,
        style: { marginBottom: '16px', display: 'flex', justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start' }
      },
        React.createElement('div', {
          style: {
            maxWidth: '72%', padding: '10px 14px', borderRadius: '12px',
            background: msg.role === 'user' ? '#1971c2' : '#2e2e2e',
            color: '#e0e0e0', fontSize: '13px', lineHeight: '1.6', whiteSpace: 'pre-wrap'
          }
        }, msg.content)
      )),
      React.createElement('div', { ref: bottomRef })
    ),
    // 输入框
    React.createElement('div', { style: { padding: '12px 16px', borderTop: '1px solid #3e3e3e', display: 'flex', gap: '8px' } },
      React.createElement('input', {
        type: 'text', value: input,
        onChange: e => setInput(e.target.value),
        onKeyDown: e => { if (e.key === 'Enter' && !e.shiftKey && !running && input.trim()) { e.preventDefault(); onSend(input); setInput(''); } },
        placeholder: running ? '运行中...' : '输入你的需求（Enter 发送）...',
        disabled: running,
        style: { flex: 1, padding: '10px 14px', background: '#2e2e2e', border: '1px solid #3e3e3e', borderRadius: '8px', color: '#e0e0e0', fontSize: '13px', outline: 'none', opacity: running ? 0.6 : 1 }
      }),
      running
        ? React.createElement('button', {
            onClick: onStop,
            style: { padding: '10px 18px', background: '#e53935', color: '#fff', border: 'none', borderRadius: '8px', cursor: 'pointer', fontSize: '13px' }
          }, '停止')
        : React.createElement('button', {
            onClick: () => { if (input.trim()) { onSend(input); setInput(''); } },
            style: { padding: '10px 18px', background: '#1971c2', color: '#fff', border: 'none', borderRadius: '8px', cursor: 'pointer', fontSize: '13px' }
          }, '发送')
    )
  );
}

// ── App ───────────────────────────────────────────────────
function App() {
  const [sessions, setSessions] = useState([{ id: '1', title: '新对话' }]);
  const [current, setCurrent] = useState('1');
  const [messages, setMessages] = useState([{ role: 'assistant', content: '你好，我是 Shiori，一个文件 agent 助手。' }]);
  const [input, setInput] = useState('');
  const [showSettings, setShowSettings] = useState(false);
  const [logs, setLogs] = useState([]);
  const [agentReady, setAgentReady] = useState(false);
  const [running, setRunning] = useState(false);

  const addLog = (msg) => setLogs(prev => [...prev, `[${new Date().toLocaleTimeString()}] ${msg}`]);

  const initAgent = async (settings) => {
    try {
      const res = await fetch(`${BACKEND}/api/init`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings)
      });
      const data = await res.json();
      if (data.status === 'ok') { setAgentReady(true); addLog('Agent 初始化成功'); }
      else { addLog(`初始化失败: ${data.error}`); }
    } catch (e) { addLog(`无法连接后端: ${e.message}`); }
  };

  useEffect(() => {
    const saved = localStorage.getItem('shiori_settings');
    if (saved) initAgent(JSON.parse(saved));
  }, []);

  const handleStop = async () => {
    await fetch(`${BACKEND}/api/stop`, { method: 'POST' });
    setRunning(false);
    addLog('已停止');
  };

  const handleSend = async (text) => {
    setMessages(prev => [...prev, { role: 'user', content: text }]);
    addLog(`发送: ${text.slice(0, 50)}`);
    setRunning(true);
    if (!agentReady) {
      setMessages(prev => [...prev, { role: 'assistant', content: '请先在设置中配置 API Key / Base URL / Model' }]);
      return;
    }

    setMessages(prev => [...prev, { role: 'assistant', content: '▌' }]);

    try {
      const res = await fetch(`${BACKEND}/api/chat`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text })
      });

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let content = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        for (const line of decoder.decode(value).split('\n')) {
          if (!line.startsWith('data:')) continue;
          const raw = line.slice(5).trim();
          if (raw === '[DONE]') continue;
          try {
            const ev = JSON.parse(raw);
            if (ev.type === 'tool_start') addLog(`🔧 ${ev.tool}\n   ${ev.input}`);
            else if (ev.type === 'tool_end') addLog(`✓ ${ev.tool}\n   ${ev.output}`);
            else if (ev.type === 'content' && ev.data) {
              content = ev.data;
              setMessages(prev => { const m = [...prev]; m[m.length - 1] = { role: 'assistant', content }; return m; });
            }
            else if (ev.type === 'error') addLog(`错误: ${ev.data}`);
          } catch (_) {}
        }
      }
      addLog('完成');
    } catch (e) {
      addLog(`请求失败: ${e.message}`);
      setMessages(prev => { const m = [...prev]; m[m.length - 1] = { role: 'assistant', content: `错误: ${e.message}` }; return m; });
    } finally {
      setRunning(false);
    }
  };

  return React.createElement('div', {
    style: { display: 'flex', height: '100vh', background: '#242424', color: '#e0e0e0', fontFamily: '-apple-system,"Segoe UI","Microsoft YaHei UI",sans-serif', fontSize: '13px' }
  },
    React.createElement(SessionList, { sessions, current, onSelect: setCurrent, onNew: () => { const id = Date.now().toString(); setSessions(prev => [...prev, { id, title: '新对话' }]); setCurrent(id); setMessages([{ role: 'assistant', content: '你好！有什么可以帮你的？' }]); }, onSettings: () => setShowSettings(true) }),
    React.createElement(ChatArea, { messages, input, setInput, onSend: handleSend, onStop: handleStop, agentReady, running }),
    React.createElement(LogPanel, { logs, onClear: () => setLogs([]) }),
    showSettings && React.createElement(SettingsModal, { onClose: () => setShowSettings(false), onSave: initAgent })
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(App));
