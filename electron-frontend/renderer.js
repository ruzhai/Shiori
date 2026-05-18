const React = require('react');
const ReactDOM = require('react-dom/client');
const { useState, useEffect, useRef } = React;

const BACKEND = 'http://localhost:5000';

// ── 辅助函数 ──────────────────────────────────────────────
function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function renderMarkdown(text) {
  if (typeof marked === 'undefined') return escapeHtml(text);
  try {
    return marked.parse(text, { breaks: true, gfm: true });
  } catch (_) {
    return escapeHtml(text);
  }
}

// ── 日志主题（颜色 / 图标）────────────────────────────────
const LOG_THEME = {
  user_message: { color: '#1971c2', icon: 'U', bg: 'rgba(25,113,194,0.15)' },
  tool_start:   { color: '#f9a825', icon: '🔧', bg: 'rgba(249,168,37,0.12)' },
  tool_end:     { color: '#66bb6a', icon: '✓', bg: 'rgba(102,187,106,0.12)' },
  error:        { color: '#e53935', icon: '!', bg: 'rgba(229,57,53,0.12)' },
  info:         { color: '#90a4ae', icon: 'i', bg: 'rgba(144,164,174,0.10)' },
  complete:     { color: '#4caf50', icon: '●', bg: 'rgba(76,175,80,0.12)' },
};

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
  const [scholarKey, setScholarKey] = useState(saved.scholar_api_key || '');
  const [useSO, setUseSO] = useState(saved.use_structured_output !== false);
  const [showTools, setShowTools] = useState(false);

  // 工具开关状态
  const toolKeys = [
    { key: 'tools_run_command', label: 'run_command - 命令行执行' },
    { key: 'tools_list_directory', label: 'list_directory - 列出目录' },
    { key: 'tools_read_file', label: 'read_file - 读取文件' },
    { key: 'tools_search_in_files', label: 'search_in_files - 搜索文件内容' },
    { key: 'tools_search_papers', label: 'search_papers - Semantic Scholar 搜索' },
    { key: 'tools_get_paper_details', label: 'get_paper_details - 论文详情' },
    { key: 'tools_get_paper_citations', label: 'get_paper_citations - 引用查询' },
    { key: 'tools_get_paper_references', label: 'get_paper_references - 参考文献' },
    { key: 'tools_read_pdf', label: 'read_pdf - PDF 阅读' },
  ];
  const [tools, setTools] = useState(() => {
    const initial = {};
    toolKeys.forEach(t => { initial[t.key] = saved[t.key] !== false; });
    return initial;
  });

  const handleSave = () => {
    const settings = { api_key: apiKey, base_url: baseUrl, model: model, scholar_api_key: scholarKey, use_structured_output: useSO, ...tools };
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
    React.createElement('div', { style: { background: '#2e2e2e', padding: '24px', borderRadius: '12px', width: '420px' } },
      React.createElement('h2', { style: { marginBottom: '16px', fontSize: '16px' } }, '设置'),
      React.createElement('input', { type: 'password', placeholder: 'API Key', value: apiKey, onChange: e => setApiKey(e.target.value), style: S.input }),
      React.createElement('input', { type: 'text', placeholder: 'Base URL (e.g. https://api.openai.com/v1)', value: baseUrl, onChange: e => setBaseUrl(e.target.value), style: S.input }),
      React.createElement('input', { type: 'text', placeholder: 'Model (e.g. gpt-4o / deepseek-reasoner)', value: model, onChange: e => setModel(e.target.value), style: S.input }),
      React.createElement('input', { type: 'password', placeholder: 'Semantic Scholar API Key (可选，1 rps 限流)', value: scholarKey, onChange: e => setScholarKey(e.target.value), style: S.input }),
      React.createElement('label', { style: { display: 'flex', alignItems: 'center', gap: '8px', color: '#a0a0a0', fontSize: '12px', marginBottom: '12px', cursor: 'pointer' } },
        React.createElement('input', { type: 'checkbox', checked: useSO, onChange: e => setUseSO(e.target.checked), style: { cursor: 'pointer' } }),
        '启用结构化输出（DeepSeek Reasoner 等推理模型请关闭）'
      ),
      // 工具开关折叠区
      React.createElement('div', { style: { marginBottom: '12px' } },
        React.createElement('button', {
          onClick: () => setShowTools(!showTools),
          style: { ...S.btn(), width: '100%', textAlign: 'left', padding: '8px 12px', fontSize: '12px', color: '#a0a0a0' }
        }, (showTools ? '▼' : '▶') + ' 工具开关'),
        showTools && React.createElement('div', {
          style: { marginTop: '8px', padding: '8px 12px', background: '#1e1e1e', borderRadius: '8px', maxHeight: '240px', overflowY: 'auto' }
        },
          toolKeys.map(tk =>
            React.createElement('label', {
              key: tk.key,
              style: { display: 'flex', alignItems: 'center', gap: '8px', color: '#a0a0a0', fontSize: '11px', padding: '3px 0', cursor: 'pointer' }
            },
              React.createElement('input', {
                type: 'checkbox',
                checked: tools[tk.key],
                onChange: e => setTools(prev => ({ ...prev, [tk.key]: e.target.checked })),
                style: { cursor: 'pointer' }
              }),
              tk.label
            )
          )
        )
      ),
      React.createElement('div', { style: { display: 'flex', gap: '8px' } },
        React.createElement('button', { onClick: handleSave, style: S.btn('#1971c2') }, '保存'),
        React.createElement('button', { onClick: onClose, style: S.btn() }, '取消')
      )
    )
  );
}

// ── SessionItem ───────────────────────────────────────────
function SessionItem({ session, isCurrent, onSelect, onDelete }) {
  const [hover, setHover] = useState(false);
  return React.createElement('div', {
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    onClick: () => onSelect(session.id),
    style: {
      padding: '10px 12px', borderRadius: '6px', cursor: 'pointer', marginBottom: '4px',
      background: isCurrent ? '#2e2e2e' : 'transparent', fontSize: '13px',
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      transition: 'background 0.15s',
    }
  },
    React.createElement('span', {
      style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }
    }, session.title),
    (hover || isCurrent) && React.createElement('button', {
      onClick: (e) => { e.stopPropagation(); onDelete(session.id); },
      style: {
        marginLeft: '6px', width: '20px', height: '20px', borderRadius: '50%',
        border: 'none', background: 'rgba(229,57,53,0.15)', color: '#e53935',
        cursor: 'pointer', fontSize: '12px', lineHeight: 1, display: 'flex',
        alignItems: 'center', justifyContent: 'center', flexShrink: 0,
      }
    }, '×')
  );
}

// ── SessionList ───────────────────────────────────────────
function SessionList({ sessions, current, onSelect, onNew, onDelete, onSettings }) {
  return React.createElement('div', {
    style: { width: '220px', borderRight: '1px solid #3e3e3e', display: 'flex', flexDirection: 'column', background: '#1e1e1e' }
  },
    React.createElement('div', { style: { padding: '12px', borderBottom: '1px solid #3e3e3e', display: 'flex', gap: '8px' } },
      React.createElement('button', { onClick: onNew, style: { ...S.btn('#1971c2'), flex: 3 } }, '+ 新建'),
      React.createElement('button', { onClick: onSettings, style: { ...S.btn(), flex: 1, padding: '8px 6px' } }, '⚙')
    ),
    React.createElement('div', { style: { flex: 1, overflowY: 'auto', padding: '8px' } },
      sessions.map(s => React.createElement(SessionItem, {
        key: s.id,
        session: s,
        isCurrent: s.id === current,
        onSelect: onSelect,
        onDelete: onDelete,
      }))
    )
  );
}

// ── LogPanel ──────────────────────────────────────────────
function LogPanel({ logs, onClear }) {
  const ref = useRef(null);
  useEffect(() => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; }, [logs]);

  return React.createElement('div', {
    style: { width: '280px', borderLeft: '1px solid #3e3e3e', display: 'flex', flexDirection: 'column', background: '#1e1e1e' }
  },
    React.createElement('div', {
      style: { padding: '10px 14px', borderBottom: '1px solid #3e3e3e', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }
    },
      React.createElement('span', { style: { fontSize: '12px', fontWeight: '600', color: '#a0a0a0' } }, '运行流程'),
      React.createElement('button', { onClick: onClear, style: { ...S.btn(), padding: '3px 8px', fontSize: '11px', flex: 'none' } }, '清空')
    ),
    React.createElement('div', {
      ref,
      style: { flex: 1, overflowY: 'auto', padding: '12px 10px', fontSize: '11px', fontFamily: '-apple-system, "Microsoft YaHei UI", sans-serif' }
    },
      logs.length === 0
        ? React.createElement('div', { style: { color: '#555', textAlign: 'center', marginTop: '20px' } }, '等待运行...')
        : logs.map((entry, i) => {
            const theme = LOG_THEME[entry.type] || LOG_THEME.info;
            const isLast = i === logs.length - 1;

            return React.createElement('div', {
              key: entry.id,
              style: { position: 'relative', paddingLeft: '24px', marginBottom: isLast ? '0' : '1px' }
            },
              // 连接线（最后一个节点不画）
              !isLast && React.createElement('div', {
                style: {
                  position: 'absolute', left: '7px', top: '20px',
                  width: '2px', height: 'calc(100% - 2px)',
                  background: '#3e3e3e'
                }
              }),
              // 圆点标记
              React.createElement('div', {
                style: {
                  position: 'absolute', left: '1px', top: '4px',
                  width: '14px', height: '14px', borderRadius: '50%',
                  background: theme.color,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: '9px', color: '#fff', lineHeight: 1, fontWeight: '700'
                }
              }, theme.icon),
              // 标签 + 时间戳
              React.createElement('div', {
                style: { display: 'flex', justifyContent: 'space-between', marginBottom: '2px' }
              },
                React.createElement('span', { style: { fontWeight: '600', color: theme.color } }, entry.label),
                React.createElement('span', { style: { color: '#555', fontSize: '10px' } }, entry.timestamp)
              ),
              // 详情（如果有内容）
              entry.detail && React.createElement('div', {
                style: {
                  color: '#888', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                  background: theme.bg, borderRadius: '4px', padding: '4px 6px',
                  marginTop: '2px', fontSize: '10px', lineHeight: '1.4',
                  maxHeight: '80px', overflowY: 'auto'
                }
              }, entry.detail)
            );
          })
    )
  );
}

// ── ChatArea ──────────────────────────────────────────────
function ChatArea({ messages, input, setInput, onSend, onStop, onConfirm, whitelist, setWhitelist, agentReady, running }) {
  const bottomRef = useRef(null);
  useEffect(() => { if (bottomRef.current) bottomRef.current.scrollIntoView({ behavior: 'smooth' }); }, [messages]);

  // 状态栏
  const statusText = running
    ? '◉ 思考中...'
    : agentReady
      ? '● Agent 就绪'
      : '● 未初始化 — 请点击设置配置 API';
  const statusColor = running ? '#4caf50' : agentReady ? '#4caf50' : '#ff9800';

  return React.createElement('div', { style: { flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 } },
    // 状态栏
    React.createElement('div', {
      style: { padding: '6px 16px', borderBottom: '1px solid #3e3e3e', fontSize: '11px', color: statusColor }
    },
      React.createElement('span', {
        style: running ? {
          display: 'inline-block',
          animation: 'pulse-dot 0.85s ease-in-out infinite',
        } : { display: 'inline-block' }
      }, statusText)
    ),
    // 消息列表
    React.createElement('div', { style: { flex: 1, overflowY: 'auto', padding: '16px' } },
      messages.map((msg, i) => {
        // 确认卡片
        if (msg.role === 'confirm') {
          const pattern = msg.command.trim().split(/\s+/)[0].toLowerCase();
          return React.createElement('div', {
            key: i,
            style: { marginBottom: '16px', display: 'flex', justifyContent: 'flex-start' }
          },
            React.createElement('div', {
              style: {
                maxWidth: '85%', padding: '12px 14px', borderRadius: '12px',
                background: '#2e2e2e', color: '#e0e0e0', fontSize: '13px',
                borderLeft: '3px solid #f9a825',
              }
            },
              msg.workingDir && React.createElement('div', {
                style: { fontSize: '10px', color: '#888', marginBottom: '6px' }
              }, '工作目录: ' + msg.workingDir),
              React.createElement('pre', {
                style: {
                  background: '#1a1a1a', border: '1px solid #3e3e3e',
                  borderRadius: '6px', padding: '10px', fontSize: '12px',
                  fontFamily: '"Consolas", "Courier New", monospace',
                  color: '#66bb6a', whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all', lineHeight: 1.4,
                  marginBottom: '10px', maxHeight: '120px', overflowY: 'auto',
                }
              }, msg.command),
              msg.resolved
                ? React.createElement('div', {
                    style: {
                      fontSize: '12px', padding: '6px 10px', borderRadius: '6px',
                      background: msg.result ? 'rgba(76,175,80,0.12)' : 'rgba(229,57,53,0.12)',
                      color: msg.result ? '#4caf50' : '#e53935',
                    }
                  }, msg.result ? '已允许 ✓' : '已拒绝 ✗')
                : React.createElement('div', { style: { display: 'flex', gap: '8px' } },
                    React.createElement('button', {
                      onClick: () => onConfirm(msg.id, false),
                      style: { flex: 1, padding: '7px 12px', background: '#e53935', color: '#fff', border: 'none', borderRadius: '6px', cursor: 'pointer', fontSize: '12px' }
                    }, '拒绝'),
                    React.createElement('button', {
                      onClick: () => onConfirm(msg.id, true),
                      style: { flex: 1, padding: '7px 12px', background: '#4caf50', color: '#fff', border: 'none', borderRadius: '6px', cursor: 'pointer', fontSize: '12px' }
                    }, '允许'),
                    React.createElement('button', {
                      onClick: () => {
                        const updated = [...whitelist, pattern];
                        setWhitelist(updated);
                        localStorage.setItem('shiori_whitelist', JSON.stringify(updated));
                        onConfirm(msg.id, true, pattern);
                      },
                      style: { flex: 1, padding: '7px 12px', background: '#3e3e3e', color: '#aaa', border: 'none', borderRadius: '6px', cursor: 'pointer', fontSize: '11px' }
                    }, '允许所有此类操作')
                  )
            )
          );
        }

        const isUser = msg.role === 'user';
        return React.createElement('div', {
          key: i,
          style: { marginBottom: '16px', display: 'flex', justifyContent: isUser ? 'flex-end' : 'flex-start' }
        },
          React.createElement('div', {
            style: {
              maxWidth: isUser ? '72%' : '85%',
              padding: '10px 14px', borderRadius: '12px',
              background: isUser ? '#1971c2' : '#2e2e2e',
              color: '#e0e0e0', fontSize: '13px', lineHeight: '1.6',
              whiteSpace: isUser ? 'pre-wrap' : 'normal',
              overflowX: 'auto',
            }
          },
            isUser
              ? msg.content
              : (msg.reasoning || msg.content)
                ? React.createElement('div', { className: 'markdown-content' },
                    // 思考过程（折叠框）
                    msg.reasoning ? React.createElement('details', {
                      style: { marginBottom: msg.content ? '8px' : '0' },
                      open: !msg.content
                    },
                      React.createElement('summary', {
                        style: { color: '#999', fontSize: '11px', cursor: 'pointer', userSelect: 'none' }
                      }, '思考过程'),
                      React.createElement('div', {
                        style: {
                          color: '#aaa', fontSize: '12px', whiteSpace: 'pre-wrap',
                          borderLeft: '2px solid #555', paddingLeft: '10px', marginTop: '6px',
                          lineHeight: '1.5'
                        }
                      }, msg.reasoning)
                    ) : null,
                    // 正文（或思考中占位）
                    msg.content
                      ? React.createElement('div', {
                          dangerouslySetInnerHTML: { __html: renderMarkdown(msg.content) }
                        })
                      : msg.reasoning
                        ? React.createElement('span', { style: { color: '#888', fontSize: '12px' } }, '思考中...')
                        : null
                  )
                : null
          )
        );
      }),
      React.createElement('div', { ref: bottomRef })
    ),
    // 输入框
    React.createElement('div', { style: { padding: '12px 16px', borderTop: '1px solid #3e3e3e', display: 'flex', gap: '8px' } },
      React.createElement('input', {
        type: 'text', value: input,
        onChange: e => setInput(e.target.value),
        onKeyDown: e => { if (e.key === 'Enter' && !e.shiftKey && !running && input.trim()) { e.preventDefault(); onSend(input); setInput(''); } },
        placeholder: running ? '思考中...' : '输入你的需求（Enter 发送）...',
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

// ── 持久化辅助函数 ─────────────────────────────────────────
const LS_SESSIONS = 'shiori_sessions';

function loadSessions() {
  try { return JSON.parse(localStorage.getItem(LS_SESSIONS)) || []; }
  catch (_) { return []; }
}
function saveSessions(sessions) {
  localStorage.setItem(LS_SESSIONS, JSON.stringify(sessions));
}
function loadMessages(threadId) {
  try { return JSON.parse(localStorage.getItem('shiori_msgs_' + threadId)) || []; }
  catch (_) { return []; }
}
function saveMessages(threadId, msgs) {
  try { localStorage.setItem('shiori_msgs_' + threadId, JSON.stringify(msgs)); }
  catch (_) {}
}

const DEFAULT_WELCOME = [{ role: 'assistant', content: '你好，我是 Shiori，一个多功能智能助手。我可以帮你搜索学术论文（Semantic Scholar）、阅读 PDF、管理文件、执行命令行操作。请在设置中配置 API 后开始使用。' }];

// ── App ───────────────────────────────────────────────────
function App() {
  const initialSessions = loadSessions();
  const hasSaved = initialSessions.length > 0;
  const initId = hasSaved ? initialSessions[0].id : '1';

  const [sessions, setSessions] = useState(
    hasSaved ? initialSessions : [{ id: '1', title: '新对话', thread_id: crypto.randomUUID(), updatedAt: Date.now() }]
  );
  const [current, setCurrent] = useState(initId);
  const [messages, setMessages] = useState(() => {
    if (hasSaved) return loadMessages(initialSessions[0].thread_id);
    return DEFAULT_WELCOME;
  });
  const [input, setInput] = useState('');
  const [showSettings, setShowSettings] = useState(false);
  const [logs, setLogs] = useState([]);
  const [agentReady, setAgentReady] = useState(false);
  const [running, setRunning] = useState(false);
  const [whitelist, setWhitelist] = useState(() => {
    try { return JSON.parse(localStorage.getItem('shiori_whitelist')) || []; }
    catch (_) { return []; }
  });

  // 首次无会话时持久化默认会话
  useEffect(() => {
    if (!hasSaved) {
      saveSessions(sessions);
    }
  }, []);

  let logIdCounter = 0;
  const addLog = (type, label, detail) => {
    const entry = {
      id: ++logIdCounter,
      type,
      label,
      detail: detail || '',
      timestamp: new Date().toLocaleTimeString(),
    };
    setLogs(prev => [...prev, entry]);
  };

  const initAgent = async (settings) => {
    try {
      const res = await fetch(`${BACKEND}/api/init`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings)
      });
      const data = await res.json();
      if (data.status === 'ok') {
        setAgentReady(true);
        const soMsg = data.use_structured_output ? '结构化输出' : '纯文本输出';
        addLog('info', 'Agent', `初始化成功 (${soMsg})`);
      }
      else { addLog('error', '初始化', data.error); }
    } catch (e) { addLog('error', '连接', `无法连接后端: ${e.message}`); }
  };

  useEffect(() => {
    const saved = localStorage.getItem('shiori_settings');
    if (saved) initAgent(JSON.parse(saved));
  }, []);

  // 切换会话时保存/加载消息
  const switchSession = (id) => {
    const curSes = sessions.find(s => s.id === current);
    if (curSes) saveMessages(curSes.thread_id, messages);
    setCurrent(id);
    setLogs([]);
    const target = sessions.find(s => s.id === id);
    if (target) {
      const msgs = loadMessages(target.thread_id);
      setMessages(msgs.length > 0 ? msgs : DEFAULT_WELCOME);
    }
  };

  const handleNew = () => {
    const curSes = sessions.find(s => s.id === current);
    if (curSes) saveMessages(curSes.thread_id, messages);
    const id = Date.now().toString();
    const thread_id = crypto.randomUUID();
    const newSes = { id, title: '新对话', thread_id, updatedAt: Date.now() };
    const updated = [newSes, ...sessions];
    setSessions(updated);
    saveSessions(updated);
    setCurrent(id);
    setMessages(DEFAULT_WELCOME);
    setLogs([]);
  };

  const handleDelete = async (id) => {
    const ses = sessions.find(s => s.id === id);
    if (ses) {
      try { await fetch(`${BACKEND}/api/threads/${ses.thread_id}`, { method: 'DELETE' }); }
      catch (_) {}
      localStorage.removeItem('shiori_msgs_' + ses.thread_id);
    }
    const updated = sessions.filter(s => s.id !== id);
    if (updated.length === 0) {
      const id2 = Date.now().toString();
      const thread_id = crypto.randomUUID();
      const newSes = { id: id2, title: '新对话', thread_id, updatedAt: Date.now() };
      updated.push(newSes);
      localStorage.removeItem('shiori_msgs_' + (ses ? ses.thread_id : ''));
    }
    setSessions(updated);
    saveSessions(updated);
    if (current === id) {
      const next = updated[0];
      setCurrent(next.id);
      setMessages(loadMessages(next.thread_id));
    }
    setLogs([]);
  };

  const handleStop = async () => {
    await fetch(`${BACKEND}/api/stop`, { method: 'POST' });
    addLog('info', '停止', '用户停止了运行');
  };

  const handleConfirm = async (reqId, approved, addWhitelist) => {
    const body = { id: reqId, approved };
    if (addWhitelist) body.add_whitelist = addWhitelist;
    try {
      await fetch(`${BACKEND}/api/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    } catch (e) {
      console.error('Confirm failed:', e);
    }
    // 更新确认卡片状态
    setMessages(prev => prev.map(m =>
      m.role === 'confirm' && m.id === reqId
        ? { ...m, resolved: true, result: approved }
        : m
    ));
  };

  const handleSend = async (text) => {
    const ses = sessions.find(s => s.id === current);
    if (!ses) return;
    const thread_id = ses.thread_id;

    setMessages(prev => [...prev, { role: 'user', content: text }]);
    addLog('user_message', '用户', text.slice(0, 100));

    // 第一条消息异步生成会话标题
    if (ses.title === '新对话') {
      fetch(`${BACKEND}/api/generate_title`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text })
      }).then(r => r.json()).then(data => {
        if (data.title && data.title !== '新对话') {
          setSessions(prev => {
            const updated = prev.map(s => s.id === current ? { ...s, title: data.title, updatedAt: Date.now() } : s);
            saveSessions(updated);
            return updated;
          });
        }
      }).catch(() => {});
    }

    setRunning(true);

    if (!agentReady) {
      setMessages(prev => [...prev, { role: 'assistant', content: '请先在设置中配置 API Key / Base URL / Model' }]);
      setRunning(false);
      return;
    }

    setMessages(prev => [...prev, { role: 'assistant', content: '', reasoning: '' }]);

    try {
      const res = await fetch(`${BACKEND}/api/chat`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, thread_id, whitelist })
      });

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let content = '';
      let reasoning = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        for (const line of decoder.decode(value).split('\n')) {
          if (!line.startsWith('data:')) continue;
          const raw = line.slice(5).trim();
          if (raw === '[DONE]') continue;
          try {
            const ev = JSON.parse(raw);
            if (ev.type === 'tool_start') addLog('tool_start', ev.tool, ev.input);
            else if (ev.type === 'tool_end') addLog('tool_end', ev.tool, ev.output);
            else if (ev.type === 'reasoning' && ev.data) {
              reasoning = ev.data;
              setMessages(prev => { const m = [...prev]; m[m.length - 1] = { role: 'assistant', content, reasoning }; return m; });
            }
            else if (ev.type === 'content' && ev.data) {
              content = ev.data;
              setMessages(prev => { const m = [...prev]; m[m.length - 1] = { role: 'assistant', content, reasoning }; return m; });
            }
            else if (ev.type === 'confirm_command') {
              addLog('info', '确认', ev.command.slice(0, 80));
              setMessages(prev => [...prev, {
                role: 'confirm',
                id: ev.id,
                command: ev.command,
                workingDir: ev.working_dir || '',
                resolved: false,
                result: null,
              }]);
            }
            else if (ev.type === 'error') addLog('error', '错误', ev.data);
          } catch (_) {}
        }
      }
      addLog('complete', '完成', content ? `回复 ${content.length} 字符` : '');
      // 完成时持久化消息
      setMessages(prev => { saveMessages(thread_id, prev); return prev; });
    } catch (e) {
      addLog('error', '请求', e.message);
      setMessages(prev => { const m = [...prev]; m[m.length - 1] = { role: 'assistant', content: '错误: ' + e.message }; return m; });
    } finally {
      setRunning(false);
    }
  };

  return React.createElement('div', {
    style: { display: 'flex', height: '100vh', background: '#242424', color: '#e0e0e0', fontFamily: '-apple-system,"Segoe UI","Microsoft YaHei UI",sans-serif', fontSize: '13px' }
  },
    React.createElement(SessionList, { sessions, current, onSelect: switchSession, onNew: handleNew, onDelete: handleDelete, onSettings: () => setShowSettings(true) }),
    React.createElement(ChatArea, { messages, input, setInput, onSend: handleSend, onStop: handleStop, onConfirm: handleConfirm, whitelist, setWhitelist, agentReady, running }),
    React.createElement(LogPanel, { logs, onClear: () => setLogs([]) }),
    showSettings && React.createElement(SettingsModal, { onClose: () => setShowSettings(false), onSave: initAgent })
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(App));
