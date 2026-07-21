// JAVIS — v4.0 Thread Tree + Clean Messaging

let ws = null, isConnected = false, isProcessing = false;
let currentState = 'idle';
let heartbeatTimer = null;
let currentThreadId = null;
let pushToTalkKey = 'F2';
let pttActive = false;
let pttEpoch = 0;

  // ── Audio unlock (workaround for browser autoplay policy) ──
  let _audioEl = null;
  let _audioCtx = null;
  function _unlockAudio() {
    if (_audioCtx) return;
    try {
      let AC = window.AudioContext || window.webkitAudioContext;
      if (AC) {
        _audioCtx = new AC();
        if (_audioCtx.state === "suspended") _audioCtx.resume();
        let buf = _audioCtx.createBuffer(1, 1, 22050);
        let src = _audioCtx.createBufferSource();
        src.buffer = buf; src.connect(_audioCtx.destination); src.start(0);
      }
    } catch(e) { console.warn("[Javis] AudioContext unlock failed:", e); }
    try {
      if (!_audioEl) {
        _audioEl = new Audio();
        _audioEl.volume = 0.01;
        _audioEl.src = 'data:audio/mp3;base64,SUQzBAAAAAAAI1RTU0UAAAAPAAADTGF2ZjU4Ljc2LjEwMAAAAAAAAAAAAAAA//tQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWGluZwAAAA8AAAACAAABhgC7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7//////////////////////////////////////////////////////////////////8AAAAATGF2YzU4LjEzAAAAAAAAAAAAAAAAJAAAAAAAAAAAAYY1TtgLAAAAAAAAAAAAAAAAAAAA';
        _audioEl.play().then(function(){
          _audioEl.pause(); _audioEl.currentTime = 0; _audioEl.volume = 1.0;
          _audioEl.src = ''; console.log('[Javis] Audio unlocked');
        }).catch(function(){ console.warn("[Javis] Audio unlock failed"); });
      }
    } catch(e) { console.warn("[Javis] Audio element unlock failed:", e); }
  }
  document.addEventListener('click', _unlockAudio, { once: true });
  document.addEventListener('keydown', _unlockAudio, { once: true });


function setBodyState(state) {
  currentState = state;
  document.body.className = document.body.className.replace(/state-\w+/g, '').trim();
  document.body.classList.add('state-' + state);
}
function setProcessing(yes) {
  isProcessing = yes;
  setBodyState(yes ? 'processing' : (isConnected ? 'idle' : 'standby'));
  if (!yes && isConnected) setJavisStatus('idle', '待命');
}

// ── Unified Status Indicator ──
function setJavisStatus(state, label) {
  let el = document.getElementById('status-text');
  if (!el) return;
  let base = 'status-dot ';
  el.textContent = (label || '');
  if (state === 'idle') {
    el.className = base + 'status-online';
  } else if (state === 'offline') {
    el.className = base + 'status-offline';
  } else {
    el.className = base + 'status-thinking';
  }
}

// ── Thread Tree ──
let threads = {};

function genId() { return 't' + Date.now() + Math.random().toString(36).slice(2, 6); }

function createThread(name, parentId) {
  let id = genId();
  let parent = parentId ? threads[parentId] : null;
  threads[id] = {
    id: id, name: name || '新对话', parentId: parentId || null,
    rootId: parent ? (parent.rootId || parent.id) : id,
    cards: [], created: new Date().toLocaleString(), children: []
  };
  if (parent) {
    if (!parent.children) parent.children = [];
    parent.children.push(id);
  }
  saveThreads();
  return id;
}

function saveThreads() { localStorage.setItem('javis_threads', JSON.stringify(threads)); }

function loadThreads() {
  try { threads = JSON.parse(localStorage.getItem('javis_threads') || '{}'); } catch (e) { threads = {}; }
  if (!Object.keys(threads).length) {
    fetch('/api/memory/conversations').then(function(r){ return r.json(); }).then(function(data){
      let convs = data.conversations || [];
      if (convs.length) {
        convs.forEach(function(c){
          let id = genId();
          threads[id] = { id: id, name: c.name || '对话', parentId: null, rootId: id, cards: [], created: c.updated_at || '', children: [] };
        });
        saveThreads();
        let keys = Object.keys(threads);
        currentThreadId = keys[keys.length - 1];
        let tid = currentThreadId;
        if (tid) {
          fetch('/api/memory/conversations/' + tid).then(function(r2){ return r2.json(); }).then(function(d2){
            if (threads[tid]) threads[tid].cards = d2.cards || [];
            saveThreads(); renderThreadTree();
          }).catch(function(){});
        }
        renderThreadTree();
        if (currentThreadId) switchThread(currentThreadId);
      } else { newRootThread(); }
    }).catch(function(){ newRootThread(); });
    return;
  }
  let keys = Object.keys(threads);
  if (keys.length) currentThreadId = keys[keys.length - 1];
}

function newRootThread() {
  let input = prompt('新对话名称：', '新对话');
  if (input === null) return;
  let id = createThread(input.trim() || '新对话', null);
  currentThreadId = id;
  document.getElementById('transcript-layer').innerHTML = '';
  showEmptyState(); renderThreadTree();
}
function resetChat() { newRootThread(); }

function deleteThread(id) {
  if (!threads[id]) return;
  let t = threads[id];
  if (!confirm('确认删除「' + (t.name || '对话') + '」？')) return;
  if (t.parentId && threads[t.parentId]) {
    threads[t.parentId].children = (threads[t.parentId].children || []).filter(function(c){ return c !== id; });
  }
  delete threads[id];
  saveThreads();
  if (id === currentThreadId) {
    let keys = Object.keys(threads);
    if (keys.length) { switchThread(keys[keys.length - 1]); }
    else { newRootThread(); }
  } else { renderThreadTree(); }
}

// ── Sidebar ──
function toggleSidebar() {
  let el = document.getElementById('sidebar');
  let wrap = document.querySelector('.sidebar-wrap');
  if (wrap) {
    wrap.style.display = wrap.style.display === 'none' ? '' : 'none';
  } else if (el) {
    el.style.display = el.style.display === 'none' ? '' : 'none';
  }
}

function renderThreadTree() {
  return; // Replaced by renderProjectTree()
  let list = document.getElementById('sidebar-list');
  if (!list) return;
  list.innerHTML = '';
  let rootIds = Object.keys(threads).filter(function(id){ return !threads[id].parentId; });
  rootIds.sort(function(a, b){ return (threads[b].created || '') > (threads[a].created || '') ? 1 : -1; });
  rootIds.forEach(function(rootId){
    let root = threads[rootId]; if (!root) return;
    renderThreadItem(list, rootId, false);
    (root.children || []).forEach(function(cid){
      if (threads[cid]) renderThreadItem(list, cid, true);
    });
  });
}

function renderThreadItem(list, id, isBranch) {
  let t = threads[id]; if (!t) return;
  let item = document.createElement('div');
  item.className = 'sidebar-item' + (id === currentThreadId ? ' active' : '');
  if (isBranch) item.style.paddingLeft = '26px';
  let span = document.createElement('span');
  span.className = 'sidebar-item-name';
  span.textContent = (isBranch ? '  ' : '') + (t.name || '对话').slice(0, 18);
  span.title = (t.name || '对话') + ' (' + (t.cards || []).length + '条)';
  span.ondblclick = function(e){
    e.stopPropagation();
    let n = prompt('重命名：', t.name || '');
    if (n && n.trim()) { t.name = n.trim(); saveThreads(); _saveToServer(); renderThreadTree(); }
  };
  item.appendChild(span);

  if (!isBranch) {
    let forkBtn = document.createElement('button');
    forkBtn.className = 'sidebar-rename-btn'; forkBtn.textContent = '+';
    forkBtn.title = '创建分支';
    forkBtn.onclick = function(e){
      e.stopPropagation();
      let inp = prompt('分支名称：', (t.name || '') + ' 分支');
      if (inp === null) return;
      let cid = createThread(inp.trim() || '分支', id);
      if (cid && threads[cid]) {
        threads[cid].cards = (t.cards || []).slice(-3);
        saveThreads();
        currentThreadId = cid;
        let layer = document.getElementById('transcript-layer');
        layer.innerHTML = '';
        let cards = threads[cid].cards || [];
        if (cards.length) { hideEmptyState(); cards.forEach(function(c){ renderCard(c.role, c.text, c.time); }); }
        else { showEmptyState(); }
        layer.scrollTop = layer.scrollHeight;
        renderThreadTree();
      }
    };
    item.appendChild(forkBtn);
  }

  let del = document.createElement('button');
  del.className = 'sidebar-del-btn'; del.textContent = 'x';
  del.onclick = function(e){ e.stopPropagation(); deleteThread(id); };
  item.appendChild(del);
  item.onclick = function(){ switchThread(id); };
  list.appendChild(item);
}

function switchThread(id) {
  if (!threads[id]) return;
  currentThreadId = id;
  let layer = document.getElementById('transcript-layer');
  layer.innerHTML = '';
  let cards = threads[id].cards || [];
  if (cards.length) { hideEmptyState(); cards.forEach(function(c){ renderCard(c.role, c.text, c.time); }); }
  else { showEmptyState(); }
  layer.scrollTop = layer.scrollHeight;
  renderThreadTree();
}

function showEmptyState() {
  let el = document.getElementById('empty-state');
  if (!el) {
    el = document.createElement('div');
    el.className = 'empty-state'; el.id = 'empty-state';
    el.innerHTML = '<div class="empty-orb"><div class="empty-orb-inner"></div></div><div class="empty-title">你好，我是 Javis</div><div class="empty-hint">你的桌面智能助手</div><div class="empty-actions"><button class="quick-btn" onclick="sendQuick(\'系统状态\')"><span>系统状态</span></button><button class="quick-btn" onclick="sendQuick(\'帮我截屏\')"><span>截取屏幕</span></button><button class="quick-btn" onclick="sendQuick(\'摄像头拍照\')"><span>摄像头拍照</span></button></div>';
    document.getElementById('transcript-layer').appendChild(el);
  }
  el.style.display = 'flex';
}
function hideEmptyState() {
  let el = document.getElementById('empty-state');
  if (el) el.style.display = 'none';
}

// ── Skills ──
async function loadSkills() {
  try { let r = await fetch('/api/skills'); let d = await r.json(); renderSkills(d.skills, d.current, d.count); } catch(e) {}
}
function renderSkills(skills, current, count) {
  let bar = document.getElementById('skill-bar');
  if (!bar) return;
  bar.innerHTML = '';
  skills.forEach(function(s){
    let tag = document.createElement('span');
    tag.className = 'skill-tag' + (s.id === current ? ' active' : '');
    tag.textContent = s.icon + ' ' + s.name;
    tag.title = s.desc || '';
    tag.onclick = function() { switchSkill(s.id); };
    bar.appendChild(tag);
  });
}
async function switchSkill(id) {
  try {
    let r = await fetch('/api/skills/activate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ skill: id }) });
    let d = await r.json();
    if (d.applied) loadSkills();
  } catch(e) {}
}

// ── Permission Level ──
let currentPermission = 'quick_auth';

async function loadPermissionLevel() {
  try {
    let r = await fetch('/api/config/permission');
    let d = await r.json();
    currentPermission = d.permission;
    renderPermission(d);
  } catch(e) { console.warn('permission load fail', e); }
}

function renderPermission(data) {
  let btns = document.querySelectorAll('.perm-btn');
  btns.forEach(function(b){ b.classList.remove('active'); });
  let active = document.querySelector('.perm-btn[data-level="' + data.permission + '"]');
  if (active) active.classList.add('active');
  let ind = document.getElementById('perm-indicator');
  if (ind) ind.textContent = data.icon + ' ' + data.label;
}

async function setPermission(level) {
  try {
    let r = await fetch('/api/config/permission', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ permission: level })
    });
    let d = await r.json();
    if (d.applied) {
      currentPermission = level;
      renderPermission(d);
      // 发送 WS 通知后端 Agent 权限级别已更新
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'permission_change', payload: { permission: level } }));
      }
    }
  } catch(e) { console.warn('permission set fail', e); }
}
function init() {
  loadThreads();
  if (currentThreadId && threads[currentThreadId]) switchThread(currentThreadId);
  connect(); initProviderListener(); initPushToTalk();
  updateStatus(); fetchStatus(); loadSkills(); renderProjectTree();
  loadModelControls(); loadPermissionLevel();
}

async function fetchStatus() {
  try {
    let r = await fetch('/api/status'); let s = await r.json();
    // populateModelDropdown removed ? using model-badge instead
    // Model is now shown via model-badge and model-select
    let badge = document.getElementById('model-badge');
    if (badge) badge.textContent = s.provider + '/' + (s.model || '?');
    let sel = document.getElementById('cfg-provider');
    if (sel && s.provider) sel.value = s.provider;
    renderModelOptions(s.provider, s.models || []);
  } catch(e) {}
}
function renderModelOptions(provider, models) {
  let container = document.getElementById('model-options'); if (!container) return;
  let group = document.getElementById('model-group'); container.innerHTML = '';
  if (provider === 'local') {
    if (group) group.style.display = 'flex';
    container.innerHTML = '<div style="flex:1;display:flex;gap:6px;align-items:center;"><input id="local-model-input" type="text" placeholder="输入模型名" style="flex:1;background:rgba(0,0,0,.3);border:1px solid var(--divider);color:var(--text);padding:7px 10px;border-radius:6px;font-size:.7rem;outline:none;"><button class="settings-btn" onclick="saveLocalModel()">切换</button></div>';
    fetch('/api/status').then(function(r){ return r.json(); }).then(function(s){ let inp = document.getElementById('local-model-input'); if (inp && s.model) inp.value = s.model; }).catch(function(){});
    return;
  }
  if (group) group.style.display = 'flex';
  if (!models || !models.length) { container.innerHTML = '<span style="font-size:.65rem;color:var(--text-tertiary);">加载中...</span>'; return; }
  models.forEach(function(m){
    let btn = document.createElement('button');
    btn.className = 'model-btn' + (m[3] ? ' active' : '');
    btn.innerHTML = '<div style="font-weight:600;font-size:.68rem;">' + m[1] + '</div><div style="font-size:.5rem;opacity:.6;">' + m[2] + '</div>';
    btn.onclick = async function(){
      container.querySelectorAll('.model-btn').forEach(function(b){ b.classList.remove('active'); });
      btn.classList.add('active');
      try {
        let r = await fetch('/api/config/model', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: provider, model: m[0] }) });
        let d = await r.json();
        if (d.applied) { document.getElementById('settings-msg').textContent = '已切换'; fetchStatus(); }
      } catch(e) {}
    };
    container.appendChild(btn);
  });
}
async function saveLocalModel() {
  let inp = document.getElementById('local-model-input');
  if (!inp || !inp.value.trim()) return;
  try {
    let r = await fetch('/api/config/model', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: 'local', model: inp.value.trim() }) });
    let d = await r.json();
    document.getElementById('settings-msg').textContent = d.applied ? '已切换' : '失败';
    if (d.applied) fetchStatus();
  } catch(e) {}
}

function updateStatus() {
  if (isConnected) {
    setBodyState(isProcessing ? 'processing' : 'idle');
    if (!isProcessing) setJavisStatus('idle', '待命');
  } else {
    setBodyState('standby');
    setJavisStatus('offline', '离线');
  }
  updateStatusBar();
}
function updateStatusBar(state, text, tool) {
  // status-bar UI was removed in the redesign; keep as no-op for compatibility
}

// ── Effort ──
const EFFORT_LEVELS = ['balanced', 'deep', 'max'];
const EFFORT_CLS = { balanced: '', deep: 'deep-thumb', max: 'max-thumb' };
const FILL_CLS = { balanced: '', deep: 'deep-fill', max: 'max-fill' };
const LABEL_CLS = { balanced: '', deep: 'deep-active', max: 'max-active' };

async function loadModelControls() {
  try {
    let ef = await fetch('/api/config/effort').then(function(r){ return r.json(); });
    let idxMap = { balanced: 0, deep: 1, max: 2 };
    let idx = idxMap[ef.effort] || 0;
    setEffort(idx);
  } catch(e) { setEffort(0); }
}

function setSliderTo(level) {
  let idxMap = { balanced: 0, deep: 1, max: 2 };
  let idx = idxMap[level] || 0;
  setEffort(idx);
}

function clickEffortTrack(event) {
  let track = document.getElementById('effort-track');
  if (!track) return;
  let rect = track.getBoundingClientRect();
  let pct = ((event.clientX - rect.left) / rect.width) * 100;
  let idx = pct < 25 ? 0 : pct < 75 ? 1 : 2;
  switchEffort(['balanced', 'deep', 'max'][idx]);
}

function powerSurge(level) {
  // Kept for compatibility — no-op with new UI
}

async function switchEffort(level) {
  try {
    let r = await fetch('/api/config/effort', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ effort: level }) });
    let d = await r.json();
    if (d.applied) {
      let idxMap = { balanced: 0, deep: 1, max: 2 };
      setEffort(idxMap[level] || 0);
    }
  } catch(e) {}
}

async function switchModel(modelName) {
  try {
    powerSurge('balanced');
    let st = await fetch('/api/status').then(function(r){ return r.json(); });
    let r = await fetch('/api/config/model', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: st.provider, model: modelName }) });
    let d = await r.json();
    if (d.applied) fetchStatus();
  } catch(e) {}
}

// Reasoning Stream — 动态思维流
// Thinking overlay — inline chat dim text
let _tsCard = null;

function tsCreate() {
  _tsCard = 'tc-' + Date.now();
  let layer = document.getElementById('transcript-layer');
  if (!layer) return;
  let div = document.createElement('div');
  div.className = 'tc';
  div.id = _tsCard;
  layer.appendChild(div);
  layer.scrollTop = layer.scrollHeight;
}

function tsStep(name) {
  let card = document.getElementById(_tsCard);
  if (!card) return;
  let span = document.createElement('span');
  span.className = 'ts';
  span.id = 'ts-' + name.replace(/\s/g, '_');
  span.textContent = name;
  card.appendChild(span);
  let layer = document.getElementById('transcript-layer');
  if (layer) layer.scrollTop = layer.scrollHeight;
}

function tsDone(name) {
  let el = document.getElementById('ts-' + name.replace(/\s/g, '_'));
  if (el) { el.className = 'ts ok'; }
}

function tsFail(name) {
  let el = document.getElementById('ts-' + name.replace(/\s/g, '_'));
  if (el) { el.className = 'ts fail'; }
}

function tsHide() {
  let card = document.getElementById(_tsCard);
  if (card) {
    card.style.transition = 'opacity .3s';
    card.style.opacity = '0';
    let cid = _tsCard;
    setTimeout(function(){
      let el = document.getElementById(cid);
      if (el && el.parentNode) el.parentNode.removeChild(el);
    }, 400);
  }
  _tsCard = null;
}


// ── WebSocket ──
function connect() {
  let p = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(p + '//' + location.host + '/ws');
  ws.onopen = function() {
    isConnected = true; updateStatus();
    if (heartbeatTimer) clearInterval(heartbeatTimer);
    heartbeatTimer = setInterval(function(){ if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping' })); }, 25000);
  };
  ws.onmessage = function(e){ handleWS(JSON.parse(e.data)); };
  ws.onclose = function() {
    isConnected = false; updateStatus();
    if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer = null; }
    setTimeout(connect, 3000);
  };
}

function handleWS(msg) {
  switch (msg.type) {
    case 'pong': break;
    case 'thinking': tsCreate(); setJavisStatus('thinking', '思考中'); break;
    case 'tool_start': tsStep(msg.tool); setJavisStatus('executing', msg.tool); break;
    case 'tool_result':
      if (msg.success) tsDone(msg.tool);
      else if (msg.tool !== 'wait' && msg.tool !== 'keyboard_press' && msg.tool !== 'keyboard_type') tsFail(msg.tool);
      // 截图和图片直接显示在对话中
      if (msg.image) {
        let layer = document.getElementById('transcript-layer');
        let img = document.createElement('img');
        img.src = 'data:image/jpeg;base64,' + msg.image;
        img.style.cssText = 'max-width:220px;border-radius:8px;margin-top:4px;display:block;';
        if (layer) layer.appendChild(img);
      }
      // 所有工具结果以折叠方式显示（不占对话空间）
      if (msg.tool !== 'end_turn' && msg.data) {
        addToolDetail(msg.data, msg.tool);
      }
      break;
    case 'voice_transcript':
      addCard('user', msg.text);
      tsCreate();
      break;
    case 'voice_text_delta':
      tsHide();
      addCard('assistant', msg.text);
      break;
    case 'audio':
      if (msg.data) playAudio(msg.data);
      break;
    case 'text_delta':
      tsHide();
      setJavisStatus('thinking', '回复中');
      appendLastCard(msg.text); break;
    case 'done':
      tsHide();
      setProcessing(false); break;
    case 'error':
      tsHide();
      setProcessing(false); break;
    case 'confirm_required':
      tsHide();
      showConfirm(msg.tool, msg.reason, msg.params); break;
  }
}


// ── 折叠式工具详情（浅灰小字，点击展开） ──
function addToolDetail(content, toolName) {
  let layer = document.getElementById('transcript-layer');
  if (!layer) return;
  let el = document.createElement('div');
  el.style.cssText = 'font-size:11px;color:#8b949e;background:#161b22;border-radius:4px;padding:3px 8px;margin:1px 0;border-left:2px solid #30363d;max-height:24px;overflow:hidden;cursor:pointer;transition:max-height .2s;line-height:1.6;';
  el.title = '点击展开 ' + (toolName || '工具详情');
  let summary = (content || '').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  let short = summary.length > 100 ? summary.slice(0,100) + '…' : summary;
  el.innerHTML = '<span style="color:#58a6ff;">▸</span> <span>' + short + '</span>';
  let expanded = false;
  el.addEventListener('click', function(e) {
    e.stopPropagation();
    expanded = !expanded;
    if (expanded) {
      el.style.maxHeight = '2000px';
      el.innerHTML = '<span style="color:#58a6ff;">▾</span> ' + summary;
    } else {
      el.style.maxHeight = '24px';
      el.innerHTML = '<span style="color:#58a6ff;">▸</span> <span>' + short + '</span>';
    }
  });
  layer.appendChild(el);
}

function updateStep(text) {
  let el = document.getElementById('chat-step');
  if (!el) return;
  // chat-step may not exist in new UI; log as fallback
  if (text) console.log('[Step]', text);
}

// ── Confirm ──
let _pendingConfirmResolve = null;
function showConfirm(tool, reason, params) {
  document.getElementById('confirm-tool').textContent = '🛠 ' + tool;
  document.getElementById('confirm-reason').textContent = reason;
  document.getElementById('confirm-params').textContent = JSON.stringify(params, null, 2);
  // 显示当前权限级别
  let badge = document.getElementById('confirm-perm-badge');
  if (badge) {
    let activeBtn = document.querySelector('.perm-btn.active');
    if (activeBtn) {
      badge.textContent = activeBtn.querySelector('.perm-icon').textContent + ' ' + activeBtn.querySelector('.perm-text').textContent;
    } else {
      badge.textContent = '';
    }
  }
  document.getElementById('confirm-modal').style.display = 'flex';
  return new Promise(function(r){ _pendingConfirmResolve = r; });
}
function approveConfirm() {
  document.getElementById('confirm-modal').style.display = 'none';
  if (_pendingConfirmResolve) { _pendingConfirmResolve(true); _pendingConfirmResolve = null; }
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'confirm', payload: { confirmed: true } }));
}
function rejectConfirm() {
  document.getElementById('confirm-modal').style.display = 'none';
  if (_pendingConfirmResolve) { _pendingConfirmResolve(false); _pendingConfirmResolve = null; }
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'confirm', payload: { confirmed: false } }));
}
function hideConfirm() { rejectConfirm(); }

// ── Send ──
function sendMessage() {
  let inp = document.getElementById('user-input'); let t = inp.value.trim();
  if (!t || isProcessing) return;
  inp.value = ''; inp.style.height = 'auto';
  if (!currentThreadId || !threads[currentThreadId]) { let id = createThread('新对话', null); currentThreadId = id; }
  tsCreate();
  addCard('user', t);
  ws.send(JSON.stringify({ type: 'message', payload: { text: t } }));
  isProcessing = true; updateStep('思考中...'); updateStatusBar('thinking', '思考中…'); setProcessing(true);
  setJavisStatus('thinking', '思考中');
}

function sendQuick(action) {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    console.warn('[sendQuick] WS not open');
    document.getElementById('user-input').value = action;
    sendMessage();
    return;
  }
  if (isProcessing) return;
  if (!currentThreadId || !threads[currentThreadId]) { let id = createThread('新对话', null); currentThreadId = id; }
  addCard('user', action);
  ws.send(JSON.stringify({ type: 'message', payload: { text: action } }));
  isProcessing = true; updateStep('思考中...'); updateStatusBar('thinking', '思考中…'); setProcessing(true);
  setJavisStatus('thinking', '思考中');
}

// ── File upload ──
function handleFileUpload(event) {
  let files = event.target.files; if (!files.length) return;
  let names = Array.from(files).map(function(f){ return f.name; }).join(', ');
  addCard('user', '📎 ' + names);
  let textFile = Array.from(files).find(function(f){ return f.type.startsWith('text/') || /\.(py|js|md|json|bat|txt|html|css|yaml|yml|toml|ini|cfg|log|xml|sh|ps1|env)$/i.test(f.name); });
  if (textFile) {
    let reader = new FileReader();
    reader.onload = function(e) {
      ws.send(JSON.stringify({ type: 'message', payload: { text: '📎 ' + names + '\n```\n' + e.target.result.slice(0, 10000) + '\n```' } }));
      isProcessing = true; updateStep('思考中...'); setProcessing(true);
    };
    reader.readAsText(textFile);
  } else {
    // Binary file (image etc): send actual data as base64
    let reader = new FileReader();
    reader.onload = function(e) {
      let b64 = e.target.result.split(',')[1];
      // 先上传图片，再发消息——确保文件存盘后再让 agent 处理
      ws.send(JSON.stringify({ type: 'image_upload', payload: { name: names, data: b64 } }));
      ws.send(JSON.stringify({ type: 'message', payload: { text: '📎 ' + names + ' (图片)' } }));
      isProcessing = true; updateStep('思考中...'); setProcessing(true);
    };
    reader.readAsDataURL(files[0]);
  }
  event.target.value = '';
}

function handleFolderUpload(event) {
  let files = event.target.files; if (!files.length) return;
  let folderName = files[0].webkitRelativePath.split('/')[0];
  let textContents = [];
  for (let fi = 0; fi < files.length; fi++) {
    let f = files[fi];
    if (f.size < 5120 && /\.(py|js|md|json|txt|html|css|yaml|yml)$/i.test(f.name)) textContents.push({ path: f.webkitRelativePath, file: f });
  }
  let sent = 0, total = textContents.length;
  addCard('user', '📁 上传: ' + folderName + ' (' + files.length + '个, ' + total + '个可传)');
  function sendNext() {
    if (sent >= total) { ws.send(JSON.stringify({ type: 'message', payload: { text: '📁 ' + folderName + ' 上传完成' } })); isProcessing = true; updateStep('思考中...'); setProcessing(true); return; }
    let item = textContents[sent];
    let r = new FileReader();
    r.onload = function(e) { ws.send(JSON.stringify({ type: 'folder_file', payload: { path: item.path, content: e.target.result } })); sent++; setTimeout(sendNext, 20); };
    r.readAsText(item.file);
  }
  sendNext(); event.target.value = '';
}

// ── Drag & Drop file upload ──
let _dropOverlay = null;
let _dropCounter = 0;

function initDragDrop() {
  _dropOverlay = document.getElementById('drop-overlay');
  if (!_dropOverlay) return;

  document.addEventListener('dragenter', function(e) {
    e.preventDefault();
    _dropCounter++;
    if (_dropCounter === 1) _dropOverlay.classList.add('active');
  });

  document.addEventListener('dragleave', function(e) {
    e.preventDefault();
    _dropCounter--;
    if (_dropCounter <= 0) { _dropCounter = 0; _dropOverlay.classList.remove('active'); }
  });

  document.addEventListener('dragover', function(e) {
    e.preventDefault();
    _dropOverlay.classList.add('drag-over');
  });

  document.addEventListener('dragend', function() {
    _dropCounter = 0; _dropOverlay.classList.remove('active', 'drag-over');
  });

  document.addEventListener('drop', function(e) {
    e.preventDefault();
    _dropCounter = 0; _dropOverlay.classList.remove('active', 'drag-over');
    let files = e.dataTransfer.files;
    if (!files || !files.length) return;
    // 用已有的上传逻辑逐个处理
    for (let i = 0; i < files.length; i++) {
      _uploadDroppedFile(files[i]);
    }
  });
}

function _uploadDroppedFile(file) {
  let names = file.name;
  let isText = file.type.startsWith('text/') || /\.(py|js|md|json|bat|txt|html|css|yaml|yml|toml|ini|cfg|log|xml|sh|ps1|env)$/i.test(file.name);
  let isImage = file.type.startsWith('image/') || /\.(png|jpg|jpeg|gif|bmp|webp)$/i.test(file.name);

  addCard('user', '📎 ' + names);
  isProcessing = true; updateStep('思考中...'); setProcessing(true);

  if (isText) {
    let reader = new FileReader();
    reader.onload = function(e) {
      ws.send(JSON.stringify({ type: 'message', payload: { text: '📎 ' + names + '\n```\n' + e.target.result.slice(0, 10000) + '\n```' } }));
    };
    reader.readAsText(file);
  } else if (isImage) {
    let reader = new FileReader();
    reader.onload = function(e) {
      let b64 = e.target.result.split(',')[1];
      // 先上传图片，再发消息——确保文件存盘后再让 agent 处理
      ws.send(JSON.stringify({ type: 'image_upload', payload: { name: names, data: b64 } }));
      ws.send(JSON.stringify({ type: 'message', payload: { text: '📎 ' + names + ' (图片)' } }));
    };
    reader.readAsDataURL(file);
  } else {
    ws.send(JSON.stringify({ type: 'message', payload: { text: '📎 ' + names + ' (二进制文件，格式可能不支持)' } }));
  }
}

// 初始化拖放
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initDragDrop);
} else {
  initDragDrop();
}

// ── Unifled Voice: click mic button = toggle real-time call, F2 = PTT real-time call ──
let voiceActive = false;          // is WS / mic active
let voiceRecorder = null;         // MediaRecorder for real-time call
let voiceStream = null;           // MediaStream for call
let voiceWs = null;               // WebSocket to /ws_voice
let voicePendingDone = false;     // true when we're waiting for server to finish

function voiceStatus(on, color) {
  let el = document.getElementById('voice-status');
  if (!el) return;
  if (on) { el.style.display = 'inline'; el.style.color = color || 'var(--accent)'; }
  else { el.style.display = 'none'; }
}

async function toggleVoice() {
  if (voiceActive) { stopVoiceCall(); return; }
  startVoiceCall();
}

async function startVoiceCall() {
  if (voiceActive) return;
  let btn = document.getElementById('voice-btn');
  try {
    voiceStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    voiceRecorder = new MediaRecorder(voiceStream, { mimeType: 'audio/webm;codecs=opus' });
  } catch(ex) {
    console.error('[Voice] Mic denied:', ex);
    alert('麦克风访问被拒绝');
    return;
  }
  try {
    voiceWs = new WebSocket((location.protocol === 'https:' ? 'wss' : 'ws') + '://' + location.host + '/ws_voice');
    let closed = false;
    function cleanupVoiceCall() {
      if (closed) return;
      closed = true; voiceActive = false;
      if (voiceRecorder && voiceRecorder.state === 'recording') try { voiceRecorder.stop(); } catch(e) {}
      if (voiceStream) { voiceStream.getTracks().forEach(function(t){ t.stop(); }); voiceStream = null; }
      if (voiceWs) { try { voiceWs.close(); } catch(e) {} voiceWs = null; }
      btn.classList.remove('call-active');
      btn.innerHTML = '<svg width="18" height="18"><use href="#i-mic"/></svg>';
      btn.title = '语音: 按 F2 说话，或点击录音';
      voiceStatus(false);
    }
    voiceWs.onopen = function() {
      voiceActive = true;
      btn.classList.add('call-active');
      btn.innerHTML = '<svg width="18" height="18"><use href="#i-pause"/></svg>';
      btn.title = '点击停止录音';
      voiceStatus(true, '#f59e0b');
      voiceRecorder.ondataavailable = function(e) {
        if (e.data.size > 0 && voiceWs && voiceWs.readyState === WebSocket.OPEN) {
          let r = new FileReader();
          r.onload = function() {
            if (voiceWs && voiceWs.readyState === WebSocket.OPEN) {
              voiceWs.send(JSON.stringify({ type: 'audio_chunk', data: r.result.split(',')[1] }));
            }
          };
          r.readAsDataURL(e.data);
        }
      };
      voiceRecorder.start(); // 无 timeslice：停止时一次性发完整音频
      voiceWs.send(JSON.stringify({ type: 'audio_start', mime: 'audio/webm;codecs=opus' }));
    };
    voiceWs.onmessage = function(e) { handleVoiceWS(JSON.parse(e.data)); };
    voiceWs.onclose = function() { cleanup(); };
    voiceWs.onerror = function() { cleanup(); };
  } catch(ex) {
    console.error('[Voice] WS Error:', ex);
    if (voiceStream) { voiceStream.getTracks().forEach(function(t){ t.stop(); }); voiceStream = null; }
  }
}

function stopVoiceCall(sendEnd) {
  voiceActive = false;
  if (voiceRecorder && voiceRecorder.state === 'recording') {
    try { voiceRecorder.stop(); } catch(e) {}
  }
  if (voiceWs && voiceWs.readyState === WebSocket.OPEN) {
    if (sendEnd !== false) {
      voiceWs.send(JSON.stringify({ type: 'audio_end' }));
    }
    try { voiceWs.close(); } catch(e) {}
  }
  if (voiceStream) { voiceStream.getTracks().forEach(function(t){ t.stop(); }); voiceStream = null; }
  let btn = document.getElementById('voice-btn');
  btn.classList.remove('call-active');
  btn.innerHTML = '<svg width="18" height="18"><use href="#i-mic"/></svg>';
  btn.title = '语音: 按 F2 说话，或点击录音';
  voiceStatus(false);
}

function handleVoiceWS(msg) {
  switch(msg.type) {
    case 'status':
      let st = msg.state || '';
      voiceStatus(true, st === 'listening' ? 'var(--accent)' : st === 'thinking' ? '#f59e0b' : st === 'speaking' ? '#10b981' : '#8b5cf6');
      setJavisStatus(st, st === 'executing' && msg.tool ? msg.tool : st);
      if (st === 'executing' && msg.tool) console.log('[Voice] Tool:', msg.tool, msg.ok ? 'OK' : '');
      break;
    case 'voice_transcript':
    case 'transcript':
      if (msg.text) addCard('user', msg.text);
      break;
    case 'text':
      if (msg.content) addCard('assistant', msg.content);
      break;
    case 'tool_start':
      tsCreate();
      tsStep(msg.tool || 'tool');
      setJavisStatus('executing', msg.tool || 'tool');
      break;
    case 'tool_result':
      if (msg.success) tsDone(msg.tool || 'tool');
      else tsFail(msg.tool || 'tool');
      if (msg.image) { addCard('assistant', 'OK ' + (msg.tool || 'tool')); }
      break;
    case 'audio':
      if (msg.data) playAudio(msg.data);
      break;
    case 'error':
      setProcessing(false);
      addCard('assistant', 'Error: ' + (msg.message || ''));
      break;
    case 'done':
      tsHide();
      setProcessing(false);
      setJavisStatus('idle', '待命');
      voiceStatus(false);
      if (!voiceActive) break;
      voiceActive = false;
      if (voiceWs) { try { voiceWs.close(); } catch(e) {} voiceWs = null; }
      if (voiceStream) { voiceStream.getTracks().forEach(function(t){ t.stop(); }); voiceStream = null; }
      let btn = document.getElementById('voice-btn');
      btn.classList.remove('call-active');
      btn.innerHTML = '<svg width="18" height="18"><use href="#i-mic"/></svg>';
      break;
    case 'pong': break;
  }
}

// ── Push-to-Talk (F2 key = hold to talk, release = send + auto-process + auto-exit) ──

function loadPTTKey() {
  try { pushToTalkKey = localStorage.getItem('javis_ptt_key') || 'F2'; }
  catch(e) { pushToTalkKey = ' '; }
}

function setPTTKey(key) {
  pushToTalkKey = key; try { localStorage.setItem('javis_ptt_key', key); } catch(e) {}
  updatePTTBadge();
}

function initPushToTalk() {
  loadPTTKey();
  document.addEventListener('keydown', function(e) {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable) return;
    if (e.key === pushToTalkKey && !e.repeat && !pttActive) {
      e.preventDefault();
      pttActive = true;
      pttEpoch++;
      let myEpoch = pttEpoch;
      pttStartStream(myEpoch);
    }
  });
  document.addEventListener('keyup', function(e) {
    if (e.key === pushToTalkKey && pttActive) {
      e.preventDefault();
      pttActive = false;
      pttStopStream();
    }
  });
  updatePTTBadge();
}

function pttStartStream(epoch) {
  if (voiceWs && voiceWs.readyState === WebSocket.OPEN) return;
  navigator.mediaDevices.getUserMedia({ audio: true }).then(function(stream) {
    if (!pttActive || pttEpoch !== epoch) { stream.getTracks().forEach(function(t){ t.stop(); }); return; }
    voiceStream = stream;
    voiceWs = new WebSocket((location.protocol === 'https:' ? 'wss' : 'ws') + '://' + location.host + '/ws_voice');
    let closed = false;
    function cleanupPTT() {
      if (closed) return;
      closed = true; pttActive = false; voiceActive = false;
      if (voiceRecorder && voiceRecorder.state === 'recording') try { voiceRecorder.stop(); } catch(e) {}
      if (voiceStream) { voiceStream.getTracks().forEach(function(t){ t.stop(); }); voiceStream = null; }
      if (voiceWs) { try { voiceWs.close(); } catch(e) {} voiceWs = null; }
      voiceStatus(false);
    }
    voiceWs.onopen = function() {
      if (!pttActive || pttEpoch !== epoch) { cleanupPTT(); return; }
      voiceActive = true;
      voiceStatus(true, '#f59e0b');
      voiceRecorder = new MediaRecorder(voiceStream, { mimeType: 'audio/webm;codecs=opus' });
      voiceRecorder.ondataavailable = function(e) {
        if (e.data.size > 0 && voiceWs && voiceWs.readyState === WebSocket.OPEN) {
          let r = new FileReader();
          r.onload = function() {
            if (voiceWs && voiceWs.readyState === WebSocket.OPEN) {
              voiceWs.send(JSON.stringify({ type: 'audio_chunk', data: r.result.split(',')[1] }));
            }
          };
          r.readAsDataURL(e.data);
        }
      };
      voiceRecorder.start(); // 无 timeslice：停止时一次性发完整音频
      voiceWs.send(JSON.stringify({ type: 'audio_start', mime: 'audio/webm;codecs=opus' }));
    };
    voiceWs.onmessage = function(e) { handleVoiceWS(JSON.parse(e.data)); };
    voiceWs.onclose = function() { cleanup(); };
    voiceWs.onerror = function() { cleanup(); };
  }).catch(function(ex) {
    console.error('[PTT] Mic denied:', ex);
    pttActive = false;
  });
}

function pttStopStream() {
  if (voiceWs && voiceWs.readyState === WebSocket.OPEN) {
    voiceWs.send(JSON.stringify({ type: 'audio_end' }));
  }
  if (voiceRecorder && voiceRecorder.state === 'recording') {
    try { voiceRecorder.stop(); } catch(e) {}
  }
  // Safety timeout: if no 'done' within 30s, force-cleanup
  if (window._pttSafetyTimer) clearTimeout(window._pttSafetyTimer);
  window._pttSafetyTimer = setTimeout(function() {
    if (voiceWs) { try { voiceWs.close(); } catch(e) {} }
    voiceStatus(false);
  }, 30000);
}

function changePTTKey(newKey) {
  if (!newKey || newKey.length > 3) { console.log('Usage: changePTTKey("Space") or changePTTKey("F2")'); return; }
  setPTTKey(newKey);
  console.log('PTT key set to:', newKey === ' ' ? 'Space' : newKey);
}

function showPTTSettings() {
  let newKey = prompt('PTT key (e.g. F2, Space):', pushToTalkKey === ' ' ? 'Space' : pushToTalkKey);
  if (!newKey) return;
  if (newKey === 'Space') newKey = ' ';
  changePTTKey(newKey);
  updatePTTBadge();
  alert('PTT key set to: ' + (newKey === ' ' ? 'Space' : newKey));
}

function updatePTTBadge() {
  let badge = document.getElementById('ptt-badge');
  if (!badge) return;
  let label = pushToTalkKey === ' ' ? 'Space' : pushToTalkKey;
  badge.textContent = 'PTT: ' + label;
}

// ── File card render ──
function renderFileCards(data) {
  if (!data) return;
  let layer = document.getElementById('transcript-layer');
  let dirMatch = data.match(/目录\s+(.+?)\s+\(/);
  let dir = dirMatch ? dirMatch[1] : '';
  let items = [];
  data.split('\n').forEach(function(l){
    let m = l.match(/^\s*📁\s+(.+?)\s+\(/);
    let f = l.match(/^\s*📄\s+(.+?)\s+\(/);
    if (m) items.push({ name: m[1], type: 'dir', path: (dir.replace(/\/$/, '') + '/' + m[1]).replace(/\/\//g, '/') });
    if (f) items.push({ name: f[1], type: 'file', path: (dir.replace(/\/$/, '') + '/' + f[1]).replace(/\/\//g, '/') });
  });
  if (!items.length) { addCard('assistant', data); return; }
  let text = '📂 ' + dir + ' (' + items.length + '项)';
  let time = new Date().toLocaleTimeString();
  if (!threads[currentThreadId]) { let id = createThread('对话', null); currentThreadId = id; }
  if (!threads[currentThreadId].cards) threads[currentThreadId].cards = [];
  threads[currentThreadId].cards.push({ role: 'assistant', text: text, time: time }); saveThreads();
  let card = document.createElement('div'); card.className = 'msg assistant';
  card.innerHTML = '<div class="msg-avatar">J</div><div class="msg-bubble"><div class="msg-meta"><span class="msg-role">Javis</span></div><b>' + text + '</b></div>';
  let grid = document.createElement('div'); grid.className = 'file-grid';
  items.forEach(function(item){
    let chip = document.createElement('button'); chip.className = 'file-chip';
    chip.textContent = (item.type === 'dir' ? '📁' : '📄') + ' ' + item.name;
    chip.onclick = function(){ ws.send(JSON.stringify({ type: 'tool', payload: { name: item.type === 'dir' ? 'file_list' : 'open_file', params: item.type === 'dir' ? { directory: item.path } : { path: item.path } } })); };
    grid.appendChild(chip);
  });
  card.querySelector('.msg-bubble').appendChild(grid);
  if (layer) layer.appendChild(card);
}

// ── Chat cards ──
function addCard(role, text) {
  try {
    let time = new Date().toLocaleTimeString();
    if (!currentThreadId || !threads[currentThreadId]) { let id = createThread('对话', null); currentThreadId = id; }
    if (!threads[currentThreadId]) { let id = createThread('对话', null); currentThreadId = id; }
    if (!threads[currentThreadId].cards) threads[currentThreadId].cards = [];
    threads[currentThreadId].cards.push({ role: role, text: text, time: time }); saveThreads();
    renderCard(role, text, time); hideEmptyState(); renderThreadTree(); _saveToServer();
  } catch(e) {
    try { let layer = document.getElementById('transcript-layer'); if (layer) { let el = document.createElement('div'); el.className = 'msg ' + role; el.innerHTML = '<div class="msg-bubble">' + escapeHtml(text) + '</div>'; layer.appendChild(el); } } catch(e2) {}
  }
}

function renderCard(role, text, time) {
  let layer = document.getElementById('transcript-layer');
  let el = document.createElement('div'); el.className = 'msg ' + role;
  let avatar = role === 'user' ? 'U' : 'J';
  let roleName = role === 'user' ? '你' : 'Javis';
  let formatted = role === 'assistant' ? renderMd(text) : '<p>' + escapeHtml(text) + '</p>';
  el.innerHTML = '<div class="msg-avatar">' + avatar + '</div><div class="msg-bubble"><div class="msg-meta"><span class="msg-role">' + roleName + '</span><span class="msg-time">' + (time || '') + '</span></div>' + formatted + '</div>';
  if (layer) { layer.appendChild(el); layer.scrollTop = layer.scrollHeight; }
}

function appendLastCard(text) {
  let cards = threads[currentThreadId]?.cards;
  if (cards && cards.length && cards[cards.length - 1].role === 'assistant') {
    cards[cards.length - 1].text += text; saveThreads();
    let bodies = document.querySelectorAll('.msg.assistant .msg-bubble');
    if (bodies.length) { bodies[bodies.length - 1].innerHTML = renderMd(cards[cards.length - 1].text); }
  } else addCard('assistant', text);
}

// ── Render MD ──
function renderMd(text) {
  let s = escapeHtml(text);
  s = s.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  s = s.replace(/^- (.+)$/gm, '<li>$1</li>'); s = s.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');
  s = s.replace(/^(\d+)\. (.+)$/gm, '<li value="$1">$2</li>'); s = s.replace(/(<li value=.*<\/li>\n?)+/g, '<ol>$&</ol>');
  s = s.replace(/\n\n/g, '</p><p>'); s = '<p>' + s + '</p>'; s = s.replace(/<p><\/p>/g, '');
  return s;
}

// ── Export ──
function exportChat(format) {
  let sid = currentThreadId;
  if (!sid || !threads[sid] || !threads[sid].cards || !threads[sid].cards.length) { document.getElementById('settings-msg').textContent = '没有可导出的对话'; return; }
  let cards = threads[sid].cards;
  if (format === 'txt') {
    let text = 'Javis 对话导出\n' + '='.repeat(40) + '\n\n';
    cards.forEach(function(c){ text += (c.role === 'user' ? '[你] ' : '[Javis] ') + c.text + '\n' + (c.time ? '  (' + c.time + ')\n' : '') + '\n'; });
    let blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    let a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'javis_' + sid + '.txt'; a.click(); URL.revokeObjectURL(a.href);
    document.getElementById('settings-msg').textContent = '已导出 TXT';
  } else if (format === 'json') {
    let data = JSON.stringify({ id: sid, cards: cards, exported_at: new Date().toISOString() }, null, 2);
    let blob = new Blob([data], { type: 'application/json;charset=utf-8' });
    let a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'javis_' + sid + '.json'; a.click(); URL.revokeObjectURL(a.href);
    document.getElementById('settings-msg').textContent = '已导出 JSON';
  }
}

function playAudio(b64) {
  if (!b64) { console.warn('[Javis] playAudio: empty data'); return; }
  try {
    if (!_audioEl) _unlockAudio();
    let el = _audioEl || new Audio();
    el.src = 'data:audio/mp3;base64,' + b64;
    let voiceBtn = document.getElementById('voice-btn');
    el.onplay = function() {
      console.log('[Javis] TTS playing...');
      if (voiceBtn) voiceBtn.style.color = '#10b981';
    };
    el.onended = function() {
      console.log('[Javis] TTS finished');
      if (voiceBtn) voiceBtn.style.color = '';
    };
    el.onerror = function(e) {
      console.error('[Javis] TTS play error:', e);
      if (voiceBtn) voiceBtn.style.color = '#f59e0b';
    };
    let p = el.play();
    if (p && p.catch) p.catch(function(e) {
      console.error('[Javis] TTS autoplay blocked:', e.message);
      _unlockAudio();
      if (voiceBtn) voiceBtn.style.color = '#f59e0b';
      try { addCard('assistant', '\u{1F50A} 语音播报被浏览器拦截，点击任意位置解锁'); } catch(ex) {}
    });
  } catch(e) { console.error('[Javis] playAudio exception:', e); }
}

// ── Settings ──
async function showSettings() {
  try {
    let r = await fetch('/api/status'); let s = await r.json();
    document.getElementById('cfg-provider').value = s.provider;
    document.getElementById('cfg-temperature').value = s.temperature; document.getElementById('temp-val').textContent = s.temperature;
    onProviderChange(); if (s.hint) document.getElementById('settings-msg').textContent = '⚠ ' + s.hint;
  } catch(e) {}
  document.getElementById('settings-modal').style.display = 'flex';
}
function hideSettings() { document.getElementById('settings-modal').style.display = 'none'; }
function onProviderChange() { document.getElementById('apikey-group').style.display = document.getElementById('cfg-provider').value === 'local' ? 'none' : 'flex'; fetchStatus(); }
async function saveApiKey() {
  let p = document.getElementById('cfg-provider').value; let k = document.getElementById('cfg-apikey').value.trim();
  if (!k) { document.getElementById('settings-msg').textContent = '❌ 输入密钥'; return; }
  try {
    let r = await fetch('/api/config/apikey', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: p, api_key: k }) });
    let d = await r.json();
    if (d.applied) { document.getElementById('settings-msg').textContent = '已生效'; document.getElementById('cfg-apikey').value = ''; fetchStatus(); }
  } catch(e) { document.getElementById('settings-msg').textContent = '❌ ' + e.message; }
}
function onTempChange() { document.getElementById('temp-val').textContent = document.getElementById('cfg-temperature').value; }
function initProviderListener() {
  document.getElementById('cfg-provider').addEventListener('change', async function(){
    let p = document.getElementById('cfg-provider').value;
    let r = await fetch('/api/config/provider', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: p }) });
    let d = await r.json(); document.getElementById('settings-msg').textContent = d.applied ? '已切换' : '失败';
    onProviderChange(); fetchStatus();
  });
}

function escapeHtml(s) { let d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

function _saveToServer() {
  try {
    let tid = currentThreadId; let t = threads[tid];
    if (tid && t) { fetch('/api/memory/conversations/' + tid, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cards: (t.cards || []).slice(-100), name: t.name || '' }) }).catch(function(){}); }
  } catch(e) {}
}

// ── Particles ──
(function(){
  let c = document.getElementById('particles'); if (!c) return;
  let ctx = c.getContext('2d'); let w, h, ps = [];
  function rs() { w = c.width = window.innerWidth; h = c.height = window.innerHeight; }
  rs(); window.addEventListener('resize', rs);
  let colors = ['rgba(10,132,255,', 'rgba(45,212,191,', 'rgba(94,234,212,'];
  for (let i = 0; i < 25; i++) ps.push({ x: Math.random() * w, y: Math.random() * h, vx: (Math.random() - .5) * .05, vy: -(Math.random() * .08 + .02), s: Math.random() * 2 + .5, c: colors[Math.floor(Math.random() * 3)], phase: Math.random() * Math.PI * 2 });
  function dr() {
    ctx.clearRect(0, 0, w, h);
    for (let i = 0; i < ps.length; i++) {
      let p = ps[i];
      p.x += p.vx + Math.sin(p.phase) * .03; p.y += p.vy; p.phase += .008;
      if (p.y < -10) { p.y = h + 10; p.x = Math.random() * w; } if (p.x < -10) p.x = w; if (p.x > w + 10) p.x = 0;
      let g = ctx.createRadialGradient(p.x - p.s * .3, p.y - p.s * .3, 0, p.x, p.y, p.s * 5);
      g.addColorStop(0, p.c + '.35)'); g.addColorStop(.3, p.c + '.10)'); g.addColorStop(1, p.c + '0)');
      ctx.beginPath(); ctx.arc(p.x, p.y, p.s * 5, 0, Math.PI * 2); ctx.fillStyle = g; ctx.fill();
      ctx.beginPath(); ctx.arc(p.x - p.s * .4, p.y - p.s * .4, p.s * .6, 0, Math.PI * 2); ctx.fillStyle = 'rgba(255,255,255,.25)'; ctx.fill();
    }
    requestAnimationFrame(dr);
  }
  dr();
})();

// ═══ Project Tree Data ═══
let PROJECT_DATA = [
  {
    name: '项目A', collapsed: false, source: 'blank', conns: [],
    convs: [
      { name: '自检报告', active: true, brs: [{ name: '主线', active: true }, { name: '只扫核心文件' }] },
      { name: '语音测试', brs: [{ name: '主线', active: true }] }
    ]
  }
];
let PROJ_ID_COUNTER = 100;
function projUid() { return 'p' + (PROJ_ID_COUNTER++); }

function renderProjectTree() {
  let list = document.getElementById('sidebar-list');
  if (!list) return;
  list.innerHTML = '';
  PROJECT_DATA.forEach(function(proj, pi) {
    let gr = document.createElement('div');
    gr.className = 'proj-group';

    let ph = document.createElement('div');
    ph.className = 'proj-header' + (proj.collapsed ? ' collapsed' : '');
    ph.onclick = function() { proj.collapsed = !proj.collapsed; renderProjectTree(); };

    let sb = proj.source === 'local' ? '<span class="proj-source-badge">📁本地</span>' : '';
    ph.innerHTML = '<span class="arrow">▾</span>' +
      '<span class="proj-icon">📁</span>' +
      '<span class="proj-name">' + escapeHtml(proj.name) + '</span>' +
      sb +
      '<span class="proj-actions">' +
        '<button class="proj-act-btn" title="新对话" onclick="event.stopPropagation();addConv(' + pi + ')">✚</button>' +
        '<button class="proj-act-btn" title="重命名" onclick="event.stopPropagation();renameProj(' + pi + ')">✎</button>' +
        '<button class="proj-act-btn" title="删除" onclick="event.stopPropagation();delProj(' + pi + ')">✕</button>' +
      '</span>';
    gr.appendChild(ph);

    let ch = document.createElement('div');
    ch.className = 'proj-children';
    if (!proj.collapsed) {
      proj.convs.forEach(function(conv, ci) {
        let cd = document.createElement('div');
        cd.className = 'conv-item' + (conv.active ? ' active' : '');
        cd.onclick = function() { setActiveConv(pi, ci); };
        cd.innerHTML = '<span class="conv-name">' + escapeHtml(conv.name) + '</span>' +
          '<span class="conv-actions">' +
            '<button class="conv-act-btn" title="创建分支" onclick="event.stopPropagation();addBranch(' + pi + ',' + ci + ')">⑂</button>' +
            '<button class="conv-act-btn" title="重命名" onclick="event.stopPropagation();renameConv(' + pi + ',' + ci + ')">✎</button>' +
            '<button class="conv-act-btn" title="删除" onclick="event.stopPropagation();delConv(' + pi + ',' + ci + ')">✕</button>' +
          '</span>';
        ch.appendChild(cd);

        if (conv.brs && conv.brs.length > 0) {
          let bd = document.createElement('div');
          bd.className = 'conv-branches';
          conv.brs.forEach(function(br, bi) {
            let be = document.createElement('div');
            be.className = 'branch-item' + (br.active ? ' active' : '');
            be.onclick = function() { setActiveBranch(pi, ci, bi); };
            be.innerHTML = '<span class="br-name">⑂ ' + escapeHtml(br.name) + '</span>';
            bd.appendChild(be);
          });
          ch.appendChild(bd);
        }
      });
    }
    gr.appendChild(ch);
    list.appendChild(gr);
  });
}

function escapeHtml(s) {
  if (!s) return '';
  return String(s).replace(/[&<>]/g, function(m) {
    return m === '&' ? '&amp;' : m === '<' ? '&lt;' : '&gt;';
  });
}

function addConv(pi) {
  let n = prompt('新对话名称：', '新对话');
  if (!n) return;
  PROJECT_DATA[pi].collapsed = false;
  PROJECT_DATA[pi].convs.forEach(function(c) {
    c.active = false;
    if (c.brs) c.brs.forEach(function(b) { b.active = false; });
  });
  PROJECT_DATA[pi].convs.push({
    name: n,
    active: true,
    brs: [{ name: '主线', active: true }]
  });
  renderProjectTree();
}

function renameProj(pi) {
  let n = prompt('重命名项目：', PROJECT_DATA[pi].name);
  if (n && n.trim()) { PROJECT_DATA[pi].name = n.trim(); renderProjectTree(); }
}

function delProj(pi) {
  if (!confirm('删除项目「' + PROJECT_DATA[pi].name + '」？')) return;
  PROJECT_DATA.splice(pi, 1);
  renderProjectTree();
}

function renameConv(pi, ci) {
  let n = prompt('重命名对话：', PROJECT_DATA[pi].convs[ci].name);
  if (n && n.trim()) { PROJECT_DATA[pi].convs[ci].name = n.trim(); renderProjectTree(); }
}

function delConv(pi, ci) {
  if (!confirm('删除对话？')) return;
  PROJECT_DATA[pi].convs.splice(ci, 1);
  renderProjectTree();
}

function addBranch(pi, ci) {
  let conv = PROJECT_DATA[pi].convs[ci];
  if (!conv.brs) conv.brs = [];
  conv.brs.forEach(function(b) { b.active = false; });
  let n = prompt('分支名：', '分支');
  if (!n) return;
  conv.brs.push({ name: n, active: true });
  renderProjectTree();
}

function setActiveConv(pi, ci) {
  PROJECT_DATA.forEach(function(p) {
    p.convs.forEach(function(c) {
      c.active = false;
      if (c.brs) c.brs.forEach(function(b) { b.active = false; });
    });
  });
  PROJECT_DATA[pi].convs[ci].active = true;
  if (PROJECT_DATA[pi].convs[ci].brs && PROJECT_DATA[pi].convs[ci].brs.length) {
    PROJECT_DATA[pi].convs[ci].brs[0].active = true;
  }
  renderProjectTree();
}

function setActiveBranch(pi, ci, bi) {
  PROJECT_DATA.forEach(function(p) {
    p.convs.forEach(function(c) {
      c.active = false;
      if (c.brs) c.brs.forEach(function(b) { b.active = false; });
    });
  });
  PROJECT_DATA[pi].convs[ci].active = true;
  PROJECT_DATA[pi].convs[ci].brs[bi].active = true;
  renderProjectTree();
}

function createNewProject() {
  PROJECT_DATA.push({
    name: '新项目',
    collapsed: false,
    source: 'blank',
    conns: [],
    convs: []
  });
  let pi = PROJECT_DATA.length - 1;
  PROJECT_DATA[pi].collapsed = false;
  let n = prompt('新对话名称：', '新对话');
  if (n) {
    PROJECT_DATA[pi].convs.push({
      name: n,
      active: true,
      brs: [{ name: '主线', active: true }]
    });
  }
  renderProjectTree();
}

function showNewProjectModal() {
  let name = prompt('新项目名称：', '新项目');
  if (!name) return;
  let mode = confirm('从本地文件夹导入？（取消 = 空白项目）');
  if (mode) {
    let path = prompt('文件夹路径：', 'D:\\projects\\' + name);
    PROJECT_DATA.push({
      name: name, collapsed: false, source: 'local', conns: [],
      convs: [
        { name: '主文件夹（' + name + '）', active: true, brs: [{ name: '主线', active: true }] },
        { name: '子文件夹', brs: [{ name: '主线', active: true }] }
      ]
    });
  } else {
    PROJECT_DATA.push({
      name: name, collapsed: false, source: 'blank', conns: [], convs: []
    });
  }
  renderProjectTree();
}

// ═══ Model Dropdown ═══
// toggleModelDropdown removed ? model-pill no longer exists in UI


// populateModelDropdown removed ? model-pill no longer exists in UI


// switchModelFromDropdown removed ? model-pill no longer exists in UI


// ── Sidebar Resize Handler ──
(function(){
  let resizer = document.getElementById('sidebar-resize');
  let sidebar = document.getElementById('sidebar');
  if (resizer && sidebar) {
    let sx, sw;
    resizer.addEventListener('mousedown', function(e) {
      sx = e.clientX; sw = sidebar.offsetWidth;
      resizer.classList.add('active');
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    });
    document.addEventListener('mousemove', function(e) {
      if (!resizer.classList.contains('active')) return;
      sidebar.style.width = Math.max(180, sw + (e.clientX - sx)) + 'px';
    });
    document.addEventListener('mouseup', function() {
      resizer.classList.remove('active');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    });
  }
})();

// Close model dropdown on outside click (legacy - no-op)


// ═══ Effort Selector ═══
function clickEffort(e) {
  let track = document.getElementById('effort-track');
  if (!track) return;
  let rect = track.getBoundingClientRect();
  let pct = ((e.clientX - rect.left) / rect.width) * 100;
  let idx = pct < 25 ? 0 : pct < 75 ? 1 : 2;
  let level = ['balanced', 'deep', 'max'][idx];
  switchEffort(level);
}

let EFFORT_LABELS = ['均衡', '深度', '最大'];
function setEffort(idx) {
  let labels = ['??', '??', '??'];
  let widths = ['0%', '50%', '100%'];
  let fill = document.getElementById('effort-fill');
  let thumb = document.getElementById('effort-thumb');
  let glow = document.getElementById('effort-glow');
  if (fill) fill.style.width = widths[idx];
  if (thumb) thumb.style.left = widths[idx];
  if (glow) glow.style.left = widths[idx];
  // Update label highlights
  for (let i = 0; i < 3; i++) {
    let lbl = document.getElementById('elabel-' + i);
    if (lbl) lbl.className = 'effort-label' + (i === idx ? ' active' : '');
  }
  let badge = document.getElementById('mode-badge');
  if (badge) badge.textContent = labels[idx];
}

document.addEventListener('DOMContentLoaded', init);

// ═══════════════════════════════════════════════════════════════
// WORKSPACE — 文件/终端/浏览器/GitHub/项目
// ═══════════════════════════════════════════════════════════════

let wsTabHistory = [];  // 浏览器历史
let wsBrowserIdx = -1;
let wsEditorPath = '';  // 当前编辑的文件路径

function toggleWorkspace() {
  let p = document.getElementById('workspace-panel');
  if (!p) return;
  p.classList.toggle('open');
  if (p.classList.contains('open') && !document.querySelector('#ws-files .ws-file-item')) {
    wsExplore('.');
    wsGitHubStatus();
    wsListProjects();
  }
}

function switchWsTab(tab) {
  document.querySelectorAll('.ws-tab').forEach(function(t){ t.classList.remove('active'); });
  document.querySelectorAll('.ws-content').forEach(function(c){ c.classList.remove('active'); });
  let activeTab = document.querySelector('.ws-tab[data-tab="' + tab + '"]');
  if (activeTab) activeTab.classList.add('active');
  let activeContent = document.getElementById('ws-' + tab);
  if (activeContent) activeContent.classList.add('active');
  // Lazy load
  if (tab === 'files' && !document.querySelector('#ws-files .ws-file-item')) wsExplore('.');
  if (tab === 'github') wsGitHubStatus();
  if (tab === 'project') wsListProjects();
}

// ── File Browser ──

function wsExplore(path) {
  let list = document.getElementById('ws-file-list');
  if (!list) return;
  list.innerHTML = '<div class="ws-loading">加载中...</div>';
  document.getElementById('ws-file-path').value = path;
  fetch('/api/workspace/explore?path=' + encodeURIComponent(path))
    .then(function(r){ return r.json(); })
    .then(function(d){
      list.innerHTML = '';
      if (!d.ok) { list.innerHTML = '<div class="ws-loading" style="color:var(--red)">' + escapeHtml(d.error) + '</div>'; return; }
      if (d.quick) {
        d.quick.forEach(function(q){
          let item = document.createElement('div');
          item.className = 'ws-file-item';
          item.innerHTML = '<span class="ws-ficon">📁</span><span class="ws-fname" style="color:var(--accent)">' + escapeHtml(q.name) + '</span>';
          item.onclick = function(){ wsExplore(q.path); };
          list.appendChild(item);
        });
      }
      d.entries.forEach(function(e){
        let item = document.createElement('div');
        item.className = 'ws-file-item';
        let icon = e.is_dir ? '📁' : (e.name.match(/\.(py|js|ts|rs|go)$/) ? '📄' : (e.name.match(/\.(jpg|png|svg|ico)$/) ? '🖼️' : (e.name.match(/\.(md|txt)$/) ? '📝' : (e.name.match(/\.(json|yaml|yml|toml)$/) ? '⚙️' : '📄'))));
        item.innerHTML = '<span class="ws-ficon">' + icon + '</span><span class="ws-fname">' + escapeHtml(e.name) + '</span><span class="ws-fsize">' + (e.is_dir ? '' : (e.size > 1024 ? Math.round(e.size/1024) + 'KB' : e.size + 'B')) + '</span>';
        item.onclick = function(){ if(e.is_dir) wsExplore(e.path); else wsOpenFile(e.path); };
        list.appendChild(item);
      });
    })
    .catch(function(err){ list.innerHTML = '<div class="ws-loading" style="color:var(--red)">请求失败: ' + err.message + '</div>'; });
}

function wsGoUp() {
  let p = document.getElementById('ws-file-path').value.replace(/\\/g, '/').replace(/\/$/, '');
  let up = p.lastIndexOf('/');
  wsExplore(up > 0 ? p.slice(0, up) : '.');
}

function wsQuickDir(dir) {
  wsExplore(dir || '.');
}

function wsOpenFile(path) {
  document.getElementById('ws-editor-name').textContent = path;
  document.getElementById('ws-file-editor').style.display = 'flex';
  let editor = document.getElementById('ws-editor-content');
  editor.value = '加载中...';
  wsEditorPath = path;
  fetch('/api/workspace/read?path=' + encodeURIComponent(path))
    .then(function(r){ return r.json(); })
    .then(function(d){
      if (d.ok) {
        if (d.binary) {
          editor.value = '[二进制文件] ' + d.name + ' (' + Math.round(d.size/1024) + 'KB)';
        } else {
          editor.value = d.content || '';
        }
      } else {
        editor.value = '错误: ' + (d.error || '');
      }
    })
    .catch(function(err){ editor.value = '请求失败: ' + err.message; });
}

function wsSaveFile() {
  if (!wsEditorPath) return;
  let content = document.getElementById('ws-editor-content').value;
  fetch('/api/workspace/save', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({path: wsEditorPath, content: content})
  })
  .then(function(r){ return r.json(); })
  .then(function(d){
    if (d.ok) {
      let hn = document.querySelector('.ws-editor-name');
      if (hn) hn.textContent = '✅ ' + wsEditorPath;
      setTimeout(function(){ if (hn) hn.textContent = wsEditorPath; }, 1500);
    }
  });
}

function wsCloseEditor() {
  document.getElementById('ws-file-editor').style.display = 'none';
  wsEditorPath = '';
}

// ── Terminal ──

function wsExecTerminal() {
  let input = document.getElementById('ws-term-input');
  let output = document.getElementById('ws-terminal-output');
  let shell = document.getElementById('ws-shell-select').value;
  let cmd = input.value.trim();
  if (!cmd) return;
  input.value = '';
  output.innerHTML += '<div class="ws-term-line"><span class="ws-prompt-purple">❯</span> ' + escapeHtml(cmd) + '</div>';
  output.scrollTop = output.scrollHeight;
  fetch('/api/workspace/terminal', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({command: cmd, shell: shell, timeout: 30})
  })
  .then(function(r){ return r.json(); })
  .then(function(d){
    if (d.output) {
      output.innerHTML += '<div class="ws-term-line" style="color:var(--text2)">' + escapeHtml(d.output.slice(0, 3000)) + '</div>';
    }
    if (d.exit_code !== undefined && d.exit_code !== 0) {
      output.innerHTML += '<div class="ws-term-line" style="color:var(--red)">exit: ' + d.exit_code + '</div>';
    }
    output.scrollTop = output.scrollHeight;
  })
  .catch(function(err){
    output.innerHTML += '<div class="ws-term-line" style="color:var(--red)">错误: ' + escapeHtml(err.message) + '</div>';
  });
}

function wsClearTerminal() {
  document.getElementById('ws-terminal-output').innerHTML = '<div class="ws-term-line"><span class="ws-prompt-purple">⚡</span> 终端已清屏</div>';
}

// ── Browser (iframe) ──

function wsNavigate() {
  let url = document.getElementById('ws-browser-url').value.trim();
  if (!url.startsWith('http://') && !url.startsWith('https://')) url = 'https://' + url;
  document.getElementById('ws-browser-url').value = url;
  let iframe = document.getElementById('ws-browser-iframe');
  if (!iframe) return;
  // Push history
  if (wsBrowserIdx < 0 || wsTabHistory[wsBrowserIdx] !== url) {
    wsTabHistory = wsTabHistory.slice(0, wsBrowserIdx + 1);
    wsTabHistory.push(url);
    wsBrowserIdx = wsTabHistory.length - 1;
  }
  try { iframe.src = url; } catch(e) { iframe.src = 'about:blank'; }
}

function wsNavBack() {
  if (wsBrowserIdx > 0) {
    wsBrowserIdx--;
    document.getElementById('ws-browser-url').value = wsTabHistory[wsBrowserIdx];
    try { document.getElementById('ws-browser-iframe').src = wsTabHistory[wsBrowserIdx]; } catch(e) {}
  }
}

function wsNavForward() {
  if (wsBrowserIdx < wsTabHistory.length - 1) {
    wsBrowserIdx++;
    document.getElementById('ws-browser-url').value = wsTabHistory[wsBrowserIdx];
    try { document.getElementById('ws-browser-iframe').src = wsTabHistory[wsBrowserIdx]; } catch(e) {}
  }
}

function wsNavRefresh() {
  let iframe = document.getElementById('ws-browser-iframe');
  if (iframe) try { iframe.src = iframe.src; } catch(e) {}
}

// ── GitHub ──

function wsGitHubStatus() {
  fetch('/api/workspace/github')
    .then(function(r){ return r.json(); })
    .then(function(d){
      let el = document.getElementById('ws-github-status');
      if (!el) return;
      if (d.available) {
        el.innerHTML = '✅ <b>gh CLI</b> ' + escapeHtml(d.version.split(' ')[0]) + (d.auth ? ' | 已登录 ' + escapeHtml(d.user) : ' | <span style="color:var(--red)">未登录</span>');
      } else {
        el.innerHTML = '⚠️ 未安装 GitHub CLI (<code>gh</code>)';
      }
    })
    .catch(function(){});
}

function wsGitHubExec(action) {
  let result = document.getElementById('ws-gh-result');
  if (!result) return;
  let cmd = '';
  if (action === 'clone') cmd = 'gh repo list --limit 5 2>&1 || echo "gh not available"';
  else if (action === 'pr') cmd = 'gh pr list --limit 5 2>&1 || echo "gh not available"';
  else if (action === 'issue') cmd = 'gh issue list --limit 5 2>&1 || echo "gh not available"';
  result.textContent = '执行中...';
  fetch('/api/workspace/terminal', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({command: cmd + ' || echo "need to install gh"', shell: 'cmd', timeout: 15})
  })
  .then(function(r){ return r.json(); })
  .then(function(d){
    result.textContent = d.output || '(无输出)';
  })
  .catch(function(err){ result.textContent = '错误: ' + err.message; });
}

// ── Projects ──

function wsCreateProject() {
  let name = document.getElementById('ws-proj-name').value.trim();
  let type = document.getElementById('ws-proj-type').value;
  if (!name) { alert('输入项目名称'); return; }
  fetch('/api/workspace/project', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({action: 'create', name: name, type: type})
  })
  .then(function(r){ return r.json(); })
  .then(function(d){
    if (d.ok) {
      document.getElementById('ws-proj-name').value = '';
      wsListProjects();
      alert('✅ 项目已创建: ' + d.path);
    } else {
      alert('❌ ' + (d.error || '创建失败'));
    }
  })
  .catch(function(err){ alert('错误: ' + err.message); });
}

function wsImportFolder() {
  let src = document.getElementById('ws-proj-source').value.trim();
  if (!src) { alert('输入源文件夹路径'); return; }
  let parts = src.replace(/\\/g, '/').split('/');
  let folderName = parts[parts.length - 1] || 'imported';
  fetch('/api/workspace/project', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({action: 'from_folder', name: folderName, source_path: src})
  })
  .then(function(r){ return r.json(); })
  .then(function(d){
    if (d.ok) {
      document.getElementById('ws-proj-source').value = '';
      wsListProjects();
      alert('✅ 已导入: ' + d.path);
    } else {
      alert('❌ ' + (d.error || '导入失败'));
    }
  })
  .catch(function(err){ alert('错误: ' + err.message); });
}

function wsListProjects() {
  fetch('/api/workspace/project', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({action: 'list'})
  })
  .then(function(r){ return r.json(); })
  .then(function(d){
    let list = document.getElementById('ws-proj-list');
    if (!list) return;
    if (d.ok && d.projects && d.projects.length) {
      list.innerHTML = '';
      d.projects.forEach(function(p){
        let item = document.createElement('div');
        item.className = 'ws-proj-item';
        item.innerHTML = '📁 ' + escapeHtml(p.name) + ' <span style="font-size:.52rem;color:var(--text3);margin-left:auto">' + p.file_count + ' 文件</span>';
        item.onclick = function(){ switchWsTab('files'); wsExplore(p.path); };
        list.appendChild(item);
      });
    } else {
      list.innerHTML = '<div class="ws-loading">暂无项目。创建一个吧 ✨</div>';
    }
  })
  .catch(function(){});
}
