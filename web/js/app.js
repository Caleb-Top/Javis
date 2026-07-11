// JAVIS — v4.0 Thread Tree + Clean Messaging

let ws = null, isConnected = false, isProcessing = false;
let mediaRecorder = null, audioChunks = [];
let currentState = 'idle';
let heartbeatTimer = null;
let currentThreadId = null;

function setBodyState(state) {
  currentState = state;
  document.body.className = document.body.className.replace(/state-\w+/g, '').trim();
  document.body.classList.add('state-' + state);
}
function setProcessing(yes) {
  isProcessing = yes;
  setBodyState(yes ? 'processing' : (isConnected ? 'idle' : 'standby'));
}

// ── Thread Tree ──
let threads = {};

function genId() { return 't' + Date.now() + Math.random().toString(36).slice(2, 6); }

function createThread(name, parentId) {
  var id = genId();
  var parent = parentId ? threads[parentId] : null;
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
      var convs = data.conversations || [];
      if (convs.length) {
        convs.forEach(function(c){
          var id = genId();
          threads[id] = { id: id, name: c.name || '对话', parentId: null, rootId: id, cards: [], created: c.updated_at || '', children: [] };
        });
        saveThreads();
        var keys = Object.keys(threads);
        currentThreadId = keys[keys.length - 1];
        var tid = currentThreadId;
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
  var keys = Object.keys(threads);
  if (keys.length) currentThreadId = keys[keys.length - 1];
}

function newRootThread() {
  var input = prompt('新对话名称：', '新对话');
  if (input === null) return;
  var id = createThread(input.trim() || '新对话', null);
  currentThreadId = id;
  document.getElementById('transcript-layer').innerHTML = '';
  showEmptyState(); renderThreadTree();
}
function resetChat() { newRootThread(); }

function deleteThread(id) {
  if (!threads[id]) return;
  var t = threads[id];
  if (!confirm('确认删除「' + (t.name || '对话') + '」？')) return;
  if (t.parentId && threads[t.parentId]) {
    threads[t.parentId].children = (threads[t.parentId].children || []).filter(function(c){ return c !== id; });
  }
  delete threads[id];
  saveThreads();
  if (id === currentThreadId) {
    var keys = Object.keys(threads);
    if (keys.length) { switchThread(keys[keys.length - 1]); }
    else { newRootThread(); }
  } else { renderThreadTree(); }
}

// ── Sidebar ──
function toggleSidebar() {
  var el = document.getElementById('sidebar');
  if (el) el.classList.toggle('collapsed');
}

function renderThreadTree() {
  var list = document.getElementById('sidebar-list');
  if (!list) return;
  list.innerHTML = '';
  var rootIds = Object.keys(threads).filter(function(id){ return !threads[id].parentId; });
  rootIds.sort(function(a, b){ return (threads[b].created || '') > (threads[a].created || '') ? 1 : -1; });
  rootIds.forEach(function(rootId){
    var root = threads[rootId]; if (!root) return;
    renderThreadItem(list, rootId, false);
    (root.children || []).forEach(function(cid){
      if (threads[cid]) renderThreadItem(list, cid, true);
    });
  });
}

function renderThreadItem(list, id, isBranch) {
  var t = threads[id]; if (!t) return;
  var item = document.createElement('div');
  item.className = 'sidebar-item' + (id === currentThreadId ? ' active' : '');
  if (isBranch) item.style.paddingLeft = '26px';
  var span = document.createElement('span');
  span.className = 'sidebar-item-name';
  span.textContent = (isBranch ? '  ' : '') + (t.name || '对话').slice(0, 18);
  span.title = (t.name || '对话') + ' (' + (t.cards || []).length + '条)';
  span.ondblclick = function(e){
    e.stopPropagation();
    var n = prompt('重命名：', t.name || '');
    if (n && n.trim()) { t.name = n.trim(); saveThreads(); _saveToServer(); renderThreadTree(); }
  };
  item.appendChild(span);

  if (!isBranch) {
    var forkBtn = document.createElement('button');
    forkBtn.className = 'sidebar-rename-btn'; forkBtn.textContent = '+';
    forkBtn.title = '创建分支';
    forkBtn.onclick = function(e){
      e.stopPropagation();
      var inp = prompt('分支名称：', (t.name || '') + ' 分支');
      if (inp === null) return;
      var cid = createThread(inp.trim() || '分支', id);
      if (cid && threads[cid]) {
        threads[cid].cards = (t.cards || []).slice(-3);
        saveThreads();
        currentThreadId = cid;
        var layer = document.getElementById('transcript-layer');
        layer.innerHTML = '';
        var cards = threads[cid].cards || [];
        if (cards.length) { hideEmptyState(); cards.forEach(function(c){ renderCard(c.role, c.text, c.time); }); }
        else { showEmptyState(); }
        layer.scrollTop = layer.scrollHeight;
        renderThreadTree();
      }
    };
    item.appendChild(forkBtn);
  }

  var del = document.createElement('button');
  del.className = 'sidebar-del-btn'; del.textContent = 'x';
  del.onclick = function(e){ e.stopPropagation(); deleteThread(id); };
  item.appendChild(del);
  item.onclick = function(){ switchThread(id); };
  list.appendChild(item);
}

function switchThread(id) {
  if (!threads[id]) return;
  currentThreadId = id;
  var layer = document.getElementById('transcript-layer');
  layer.innerHTML = '';
  var cards = threads[id].cards || [];
  if (cards.length) { hideEmptyState(); cards.forEach(function(c){ renderCard(c.role, c.text, c.time); }); }
  else { showEmptyState(); }
  layer.scrollTop = layer.scrollHeight;
  renderThreadTree();
}

function showEmptyState() {
  var el = document.getElementById('empty-state');
  if (!el) {
    el = document.createElement('div');
    el.className = 'empty-state'; el.id = 'empty-state';
    el.innerHTML = '<div class="empty-orb"><div class="empty-orb-inner"></div></div><div class="empty-title">你好，我是 Javis</div><div class="empty-hint">你的桌面智能助手</div><div class="empty-actions"><button class="quick-btn" onclick="sendQuick(\'系统状态\')"><span>系统状态</span></button><button class="quick-btn" onclick="sendQuick(\'帮我截屏\')"><span>截取屏幕</span></button><button class="quick-btn" onclick="sendQuick(\'摄像头拍照\')"><span>摄像头拍照</span></button></div>';
    document.getElementById('transcript-layer').appendChild(el);
  }
  el.style.display = 'flex';
}
function hideEmptyState() {
  var el = document.getElementById('empty-state');
  if (el) el.style.display = 'none';
}

// ── Skills ──
async function loadSkills() {
  try { var r = await fetch('/api/skills'); var d = await r.json(); renderSkills(d.skills, d.current, d.count); } catch(e) {}
}
function renderSkills(skills, current, count) {
  var bar = document.getElementById('skill-bar'); if (!bar) return;
  bar.innerHTML = '';
  skills.forEach(function(s){
    var pill = document.createElement('button');
    pill.className = 'skill-pill' + (s.id === current ? ' active' : '');
    pill.textContent = s.icon + ' ' + s.name;
    pill.onclick = function(){ switchSkill(s.id); };
    bar.appendChild(pill);
  });
  document.querySelectorAll('.tool-badge').forEach(function(el){ el.textContent = count + ' 工具'; });
}
async function switchSkill(id) {
  try {
    var r = await fetch('/api/skills/activate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ skill: id }) });
    var d = await r.json();
    if (d.applied) loadSkills();
  } catch(e) {}
}

// ── Permission Level ──
let currentPermission = 'quick_auth';

async function loadPermissionLevel() {
  try {
    var r = await fetch('/api/config/permission');
    var d = await r.json();
    currentPermission = d.permission;
    renderPermission(d);
  } catch(e) { console.warn('permission load fail', e); }
}

function renderPermission(data) {
  var btns = document.querySelectorAll('.perm-btn');
  btns.forEach(function(b){ b.classList.remove('active'); });
  var active = document.querySelector('.perm-btn[data-level="' + data.permission + '"]');
  if (active) active.classList.add('active');
  var ind = document.getElementById('perm-indicator');
  if (ind) ind.textContent = data.icon + ' ' + data.label;
}

async function setPermission(level) {
  try {
    var r = await fetch('/api/config/permission', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ permission: level })
    });
    var d = await r.json();
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
  connect(); initProviderListener();
  updateStatus(); fetchStatus(); loadSkills(); renderThreadTree();
  loadModelControls(); loadPermissionLevel();
}

async function fetchStatus() {
  try {
    var r = await fetch('/api/status'); var s = await r.json();
    var badge = document.getElementById('model-badge');
    if (badge) badge.textContent = s.provider + '/' + (s.model || '?');
    var sel = document.getElementById('cfg-provider');
    if (sel && s.provider) sel.value = s.provider;
    renderModelOptions(s.provider, s.models || []);
  } catch(e) {}
}
function renderModelOptions(provider, models) {
  var container = document.getElementById('model-options'); if (!container) return;
  var group = document.getElementById('model-group'); container.innerHTML = '';
  if (provider === 'local') {
    if (group) group.style.display = 'flex';
    container.innerHTML = '<div style="flex:1;display:flex;gap:6px;align-items:center;"><input id="local-model-input" type="text" placeholder="输入模型名" style="flex:1;background:rgba(0,0,0,.3);border:1px solid var(--divider);color:var(--text);padding:7px 10px;border-radius:6px;font-size:.7rem;outline:none;"><button class="settings-btn" onclick="saveLocalModel()">切换</button></div>';
    fetch('/api/status').then(function(r){ return r.json(); }).then(function(s){ var inp = document.getElementById('local-model-input'); if (inp && s.model) inp.value = s.model; }).catch(function(){});
    return;
  }
  if (group) group.style.display = 'flex';
  if (!models || !models.length) { container.innerHTML = '<span style="font-size:.65rem;color:var(--text-tertiary);">加载中...</span>'; return; }
  models.forEach(function(m){
    var btn = document.createElement('button');
    btn.className = 'model-btn' + (m[3] ? ' active' : '');
    btn.innerHTML = '<div style="font-weight:600;font-size:.68rem;">' + m[1] + '</div><div style="font-size:.5rem;opacity:.6;">' + m[2] + '</div>';
    btn.onclick = async function(){
      container.querySelectorAll('.model-btn').forEach(function(b){ b.classList.remove('active'); });
      btn.classList.add('active');
      try {
        var r = await fetch('/api/config/model', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: provider, model: m[0] }) });
        var d = await r.json();
        if (d.applied) { document.getElementById('settings-msg').textContent = '已切换'; fetchStatus(); }
      } catch(e) {}
    };
    container.appendChild(btn);
  });
}
async function saveLocalModel() {
  var inp = document.getElementById('local-model-input');
  if (!inp || !inp.value.trim()) return;
  try {
    var r = await fetch('/api/config/model', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: 'local', model: inp.value.trim() }) });
    var d = await r.json();
    document.getElementById('settings-msg').textContent = d.applied ? '已切换' : '失败';
    if (d.applied) fetchStatus();
  } catch(e) {}
}

function updateStatus() {
  var el = document.getElementById('status-text');
  if (!el) return;
  el.textContent = isConnected ? '●' : '○';
  el.style.color = isConnected ? '#30d158' : '#ff453a';
  if (isConnected && !isProcessing) setBodyState('idle');
  else if (!isConnected) setBodyState('standby');
  updateStatusBar();
}
function updateStatusBar(state, text, tool) {
  var bar = document.getElementById('status-bar'); if (!bar) return;
  if (!state || state === 'idle') { bar.style.display = 'none'; return; }
  bar.style.display = 'flex';
  document.getElementById('status-bar-text').textContent = text || '';
  var toolEl = document.getElementById('status-bar-tool');
  if (toolEl) { toolEl.textContent = tool || ''; toolEl.style.display = tool ? '' : 'none'; }
}

// ── Effort ──
const EFFORT_LEVELS = ['balanced', 'deep', 'max'];
const EFFORT_CLS = { balanced: '', deep: 'deep-thumb', max: 'max-thumb' };
const FILL_CLS = { balanced: '', deep: 'deep-fill', max: 'max-fill' };
const LABEL_CLS = { balanced: '', deep: 'deep-active', max: 'max-active' };

async function loadModelControls() {
  try {
    var st = await fetch('/api/status').then(function(r){ return r.json(); });
    var sel = document.getElementById('model-select');
    if (sel && st.models) {
      sel.innerHTML = '';
      st.models.forEach(function(m){
        var o = document.createElement('option'); o.value = m[0]; o.textContent = m[1]; o.selected = m[3]; sel.appendChild(o);
      });
    }
    var mn = document.getElementById('mode-model-name');
    if (mn) mn.textContent = st.model || '';
    var mb = document.getElementById('mode-badge');
    if (mb) mb.textContent = st.provider;
    var ef = await fetch('/api/config/effort').then(function(r){ return r.json(); });
    setSliderTo(ef.effort);
  } catch(e) { setSliderTo('balanced'); }
}

function setSliderTo(level) {
  var pct = { balanced: '16%', deep: '50%', max: '84%' };
  var thumb = document.getElementById('effort-thumb');
  if (thumb) { thumb.style.left = pct[level]; thumb.className = 'effort-thumb' + (EFFORT_CLS[level] ? ' ' + EFFORT_CLS[level] : ''); }
  var fill = document.getElementById('effort-fill');
  if (fill) { fill.style.width = pct[level]; fill.className = 'effort-track-fill' + (FILL_CLS[level] ? ' ' + FILL_CLS[level] : ''); }
  EFFORT_LEVELS.forEach(function(k, i){
    var l = document.getElementById('elabel-' + i);
    if (l) l.className = 'effort-label' + (k === level ? ' active' : '') + (LABEL_CLS[k] && k === level ? ' ' + LABEL_CLS[k] : '');
  });
}

function clickEffortTrack(event) {
  var track = document.getElementById('effort-track');
  if (!track) return;
  var rect = track.getBoundingClientRect();
  var pct = ((event.clientX - rect.left) / rect.width) * 100;
  switchEffort(pct < 40 ? 'balanced' : pct < 80 ? 'deep' : 'max');
}

function powerSurge(level) {
  var el = document.getElementById('effort-glow');
  if (!el) return;
  el.className = 'effort-glow' + (level === 'deep' ? ' deep-glow' : '') + (level === 'max' ? ' max-glow' : '');
}

async function switchEffort(level) {
  try {
    powerSurge(level);
    var r = await fetch('/api/config/effort', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ effort: level }) });
    var d = await r.json();
    if (d.applied) { setSliderTo(level); fetchStatus(); }
  } catch(e) {}
}

async function switchModel(modelName) {
  try {
    powerSurge('balanced');
    var st = await fetch('/api/status').then(function(r){ return r.json(); });
    var r = await fetch('/api/config/model', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: st.provider, model: modelName }) });
    var d = await r.json();
    if (d.applied) fetchStatus();
  } catch(e) {}
}

// ── Progress Card ──
let _progressStepId = 0;

function initProgressCard(title) {
  var layer = document.getElementById('transcript-layer');
  var old = document.getElementById('progress-card'); if (old) old.remove();
  _progressStepId = 0;
  var card = document.createElement('div');
  card.className = 'progress-card'; card.id = 'progress-card';
  card.innerHTML = '<div class="progress-header" onclick="toggleProgressBody()"><span class="progress-dot"></span><span class="progress-title">' + escapeHtml(title) + '</span></div><div class="progress-body open" id="progress-body"><div class="progress-steps" id="progress-steps"></div></div>';
  layer.appendChild(card); layer.scrollTop = layer.scrollHeight;
}

function addProgressStep(toolName, params) {
  _progressStepId++; var id = _progressStepId;
  var container = document.getElementById('progress-steps'); if (!container) return id;
  var ps = params ? Object.values(params).filter(function(v){ return typeof v === 'string' && v.length < 60; }).join(' ') : '';
  var step = document.createElement('div');
  step.className = 'progress-step active'; step.id = 'pstep-' + id;
  step.textContent = ps ? toolName + ': ' + ps : toolName;
  container.appendChild(step); return id;
}

function completeProgressStep(id, data) {
  var step = document.getElementById('pstep-' + id); if (!step) return;
  step.className = 'progress-step done';
  if (data) {
    var lines = data.split('\n').filter(function(l){ return l.trim(); });
    var snippet = lines.slice(0, 2).join(' · ');
    if (snippet.length > 120) snippet = snippet.slice(0, 120) + '...';
    if (snippet) {
      var d = document.createElement('div');
      d.className = 'progress-step-detail'; d.textContent = snippet;
      step.parentNode.insertBefore(d, step.nextSibling);
    }
  }
}

function toggleProgressBody() {
  var body = document.getElementById('progress-body');
  if (body) body.classList.toggle('open');
}

function removeProgressCard() {
  var card = document.getElementById('progress-card');
  if (card) { card.style.transition = 'opacity .2s'; card.style.opacity = '0'; setTimeout(function(){ card.remove(); }, 200); }
}

// ── WebSocket ──
function connect() {
  var p = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(p + '//' + location.host + '/ws');
  ws.onopen = function() {
    isConnected = true; updateStatus(); updateStatusBar('idle');
    if (heartbeatTimer) clearInterval(heartbeatTimer);
    heartbeatTimer = setInterval(function(){ if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping' })); }, 25000);
  };
  ws.onmessage = function(e){ handleWS(JSON.parse(e.data)); };
  ws.onclose = function() {
    isConnected = false; updateStatus(); updateStatusBar('error', '断开');
    if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer = null; }
    setTimeout(connect, 3000);
  };
}

let _currentProgressStepId = 0;

function handleWS(msg) {
  switch (msg.type) {
    case 'pong': break;
    case 'thinking': updateStep('思考中...'); updateStatusBar('thinking', '思考中…'); break;
    case 'tool_start':
      updateStep(msg.tool); updateStatusBar('executing', '执行…', msg.tool);
      _currentProgressStepId = addProgressStep(msg.tool, msg.params); break;
    case 'tool_result':
      if (msg.tool === 'wait' || msg.tool === 'keyboard_press' || msg.tool === 'keyboard_type') break;
      if (msg.tool === 'screenshot' && msg.success) { if (_currentProgressStepId) completeProgressStep(_currentProgressStepId, ''); addCard('assistant', '📸 截图完成'); break; }
      if (msg.image) {
        if (_currentProgressStepId) completeProgressStep(_currentProgressStepId, '');
        addCard('assistant', '✅ ' + msg.tool);
        var layer = document.getElementById('transcript-layer');
        var img = document.createElement('img');
        img.src = 'data:image/jpeg;base64,' + msg.image;
        img.style.cssText = 'max-width:220px;border-radius:8px;margin-top:4px;';
        if (layer) layer.appendChild(img);
      } else if (msg.tool === 'file_list' || msg.tool === 'file_read') {
        if (_currentProgressStepId) completeProgressStep(_currentProgressStepId, msg.data);
        renderFileCards(msg.data);
      } else if (msg.tool === 'run_code') {
        if (_currentProgressStepId) completeProgressStep(_currentProgressStepId, msg.data);
        var lines = msg.data ? msg.data.split('\n').filter(function(l){ return l.trim(); }) : [];
        var summary = lines.filter(function(l){ return l.length > 10 && !/^[=\-*•\s]+$/.test(l); }).slice(0, 2).join(' | ');
        if (summary.length > 100) summary = summary.slice(0, 100) + '…';
        addCard('assistant', '💻 run_code' + (summary ? '\n' + summary : ''));
      } else {
        if (_currentProgressStepId) completeProgressStep(_currentProgressStepId, msg.data);
        var d = (msg.success ? '✅ ' : '❌ ') + msg.tool;
        if (msg.data) { var ls = msg.data.split('\n'); d += '\n' + ls.slice(0, 2).join('\n'); }
        addCard('assistant', d);
      }
      break;
    case 'text_delta':
      updateStep(''); updateStatusBar('thinking', '生成回复…');
      appendLastCard(msg.text); break;
    case 'confirm_required':
      updateStatusBar('idle', '等待确认…'); showConfirm(msg.tool, msg.reason, msg.params); break;
    case 'done':
      updateStep(''); updateStatusBar('idle'); setProcessing(false);
      setTimeout(removeProgressCard, 2000); break;
    case 'error':
      updateStep(''); updateStatusBar('error', '操作失败'); setProcessing(false); break;
  }
}

function updateStep(text) {
  var el = document.getElementById('chat-step');
  if (!el) return;
  el.innerHTML = text ? '<span class="step-dot"></span> ' + escapeHtml(text) : '';
}

// ── Confirm ──
let _pendingConfirmResolve = null;
function showConfirm(tool, reason, params) {
  document.getElementById('confirm-tool').textContent = '🛠 ' + tool;
  document.getElementById('confirm-reason').textContent = reason;
  document.getElementById('confirm-params').textContent = JSON.stringify(params, null, 2);
  // 显示当前权限级别
  var badge = document.getElementById('confirm-perm-badge');
  if (badge) {
    var activeBtn = document.querySelector('.perm-btn.active');
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
  var inp = document.getElementById('user-input'); var t = inp.value.trim();
  if (!t || isProcessing) return;
  inp.value = ''; inp.style.height = 'auto';
  if (!currentThreadId || !threads[currentThreadId]) { var id = createThread('新对话', null); currentThreadId = id; }
  initProgressCard(t.length > 30 ? t.slice(0, 30) + '…' : t);
  addProgressStep('需求', {});
  addCard('user', t);
  ws.send(JSON.stringify({ type: 'message', payload: { text: t } }));
  isProcessing = true; updateStep('思考中...'); updateStatusBar('thinking', '思考中…'); setProcessing(true);
}

function sendQuick(action) {
  var map = { '帮我截屏': ['screenshot', {}], '系统状态': ['system_info', {}], '摄像头拍照': ['camera_snapshot', {}] };
  var cmd = map[action];
  if (!cmd) { document.getElementById('user-input').value = action; sendMessage(); return; }
  if (!isConnected) return;
  isProcessing = true; updateStep('执行' + cmd[0]); updateStatusBar('executing', '执行…', cmd[0]);
  setProcessing(true); addCard('user', action);
  ws.send(JSON.stringify({ type: 'tool', payload: { name: cmd[0], params: cmd[1] } }));
}

// ── File upload ──
function handleFileUpload(event) {
  var files = event.target.files; if (!files.length) return;
  var names = Array.from(files).map(function(f){ return f.name; }).join(', ');
  addCard('user', '📎 ' + names);
  var textFile = Array.from(files).find(function(f){ return f.type.startsWith('text/') || /\.(py|js|md|json|bat|txt|html|css|yaml|yml|toml|ini|cfg|log|xml|sh|ps1|env)$/i.test(f.name); });
  if (textFile) {
    var reader = new FileReader();
    reader.onload = function(e) {
      ws.send(JSON.stringify({ type: 'message', payload: { text: '📎 ' + names + '\n```\n' + e.target.result.slice(0, 10000) + '\n```' } }));
      isProcessing = true; updateStep('思考中...'); setProcessing(true);
    };
    reader.readAsText(textFile);
  } else {
    ws.send(JSON.stringify({ type: 'message', payload: { text: '📎 ' + names + ' (二进制)' } }));
    isProcessing = true; updateStep('思考中...'); setProcessing(true);
  }
  event.target.value = '';
}

function handleFolderUpload(event) {
  var files = event.target.files; if (!files.length) return;
  var folderName = files[0].webkitRelativePath.split('/')[0];
  var textContents = [];
  for (var fi = 0; fi < files.length; fi++) {
    var f = files[fi];
    if (f.size < 5120 && /\.(py|js|md|json|txt|html|css|yaml|yml)$/i.test(f.name)) textContents.push({ path: f.webkitRelativePath, file: f });
  }
  var sent = 0, total = textContents.length;
  addCard('user', '📁 上传: ' + folderName + ' (' + files.length + '个, ' + total + '个可传)');
  function sendNext() {
    if (sent >= total) { ws.send(JSON.stringify({ type: 'message', payload: { text: '📁 ' + folderName + ' 上传完成' } })); isProcessing = true; updateStep('思考中...'); setProcessing(true); return; }
    var item = textContents[sent];
    var r = new FileReader();
    r.onload = function(e) { ws.send(JSON.stringify({ type: 'folder_file', payload: { path: item.path, content: e.target.result } })); sent++; setTimeout(sendNext, 20); };
    r.readAsText(item.file);
  }
  sendNext(); event.target.value = '';
}

// ── Voice ──
async function toggleVoice() {
  var btn = document.getElementById('voice-btn');
  var indicator = document.getElementById('voice-indicator');
  var vt = document.getElementById('voice-text');
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop(); btn.classList.remove('recording');
    btn.innerHTML = '<svg width="18" height="18"><use href="#i-mic"/></svg>';
    indicator.classList.remove('active'); return;
  }
  try {
    var stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream); audioChunks = [];
    mediaRecorder.ondataavailable = function(e){ audioChunks.push(e.data); };
    mediaRecorder.onstop = async function() {
      var blob = new Blob(audioChunks, { type: 'audio/webm' });
      var r = new FileReader();
      r.onload = function() { ws.send(JSON.stringify({ type: 'voice', payload: { audio: r.result.split(',')[1] } })); if (vt) vt.textContent = '识别中...'; indicator.classList.add('active'); };
      r.readAsDataURL(blob); stream.getTracks().forEach(function(t){ t.stop(); });
    };
    mediaRecorder.start(); btn.classList.add('recording');
    btn.innerHTML = '<svg width="18" height="18"><use href="#i-pause"/></svg>';
    if (vt) vt.textContent = '点击停止'; indicator.classList.add('active');
    setTimeout(function(){ if (mediaRecorder && mediaRecorder.state === 'recording') { mediaRecorder.stop(); btn.classList.remove('recording'); btn.innerHTML = '<svg width="18" height="18"><use href="#i-mic"/></svg>'; indicator.classList.remove('active'); } }, 30000);
  } catch(e) { alert('麦克风访问被拒绝'); }
}

// ── File card render ──
function renderFileCards(data) {
  if (!data) return;
  var layer = document.getElementById('transcript-layer');
  var dirMatch = data.match(/目录\s+(.+?)\s+\(/);
  var dir = dirMatch ? dirMatch[1] : '';
  var items = [];
  data.split('\n').forEach(function(l){
    var m = l.match(/^\s*📁\s+(.+?)\s+\(/);
    var f = l.match(/^\s*📄\s+(.+?)\s+\(/);
    if (m) items.push({ name: m[1], type: 'dir', path: (dir.replace(/\/$/, '') + '/' + m[1]).replace(/\/\//g, '/') });
    if (f) items.push({ name: f[1], type: 'file', path: (dir.replace(/\/$/, '') + '/' + f[1]).replace(/\/\//g, '/') });
  });
  if (!items.length) { addCard('assistant', data); return; }
  var text = '📂 ' + dir + ' (' + items.length + '项)';
  var time = new Date().toLocaleTimeString();
  if (!threads[currentThreadId]) { var id = createThread('对话', null); currentThreadId = id; }
  if (!threads[currentThreadId].cards) threads[currentThreadId].cards = [];
  threads[currentThreadId].cards.push({ role: 'assistant', text: text, time: time }); saveThreads();
  var card = document.createElement('div'); card.className = 'msg assistant';
  card.innerHTML = '<div class="msg-avatar">J</div><div class="msg-bubble"><div class="msg-meta"><span class="msg-role">Javis</span></div><b>' + text + '</b></div>';
  var grid = document.createElement('div'); grid.className = 'file-grid';
  items.forEach(function(item){
    var chip = document.createElement('button'); chip.className = 'file-chip';
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
    var time = new Date().toLocaleTimeString();
    if (!currentThreadId || !threads[currentThreadId]) { var id = createThread('对话', null); currentThreadId = id; }
    if (!threads[currentThreadId]) { var id = createThread('对话', null); currentThreadId = id; }
    if (!threads[currentThreadId].cards) threads[currentThreadId].cards = [];
    threads[currentThreadId].cards.push({ role: role, text: text, time: time }); saveThreads();
    renderCard(role, text, time); hideEmptyState(); renderThreadTree(); _saveToServer();
  } catch(e) {
    try { var layer = document.getElementById('transcript-layer'); if (layer) { var el = document.createElement('div'); el.className = 'msg ' + role; el.innerHTML = '<div class="msg-bubble">' + escapeHtml(text) + '</div>'; layer.appendChild(el); } } catch(e2) {}
  }
}

function renderCard(role, text, time) {
  var layer = document.getElementById('transcript-layer');
  var el = document.createElement('div'); el.className = 'msg ' + role;
  var avatar = role === 'user' ? 'U' : 'J';
  var roleName = role === 'user' ? '你' : 'Javis';
  var formatted = role === 'assistant' ? renderMd(text) : '<p>' + escapeHtml(text) + '</p>';
  el.innerHTML = '<div class="msg-avatar">' + avatar + '</div><div class="msg-bubble"><div class="msg-meta"><span class="msg-role">' + roleName + '</span><span class="msg-time">' + (time || '') + '</span></div>' + formatted + '</div>';
  if (layer) { layer.appendChild(el); layer.scrollTop = layer.scrollHeight; }
}

function appendLastCard(text) {
  var cards = threads[currentThreadId]?.cards;
  if (cards && cards.length && cards[cards.length - 1].role === 'assistant') {
    cards[cards.length - 1].text += text; saveThreads();
    var bodies = document.querySelectorAll('.msg.assistant .msg-bubble');
    if (bodies.length) { bodies[bodies.length - 1].innerHTML = renderMd(cards[cards.length - 1].text); }
  } else addCard('assistant', text);
}

// ── Render MD ──
function renderMd(text) {
  var s = escapeHtml(text);
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
  var sid = currentThreadId;
  if (!sid || !threads[sid] || !threads[sid].cards || !threads[sid].cards.length) { document.getElementById('settings-msg').textContent = '没有可导出的对话'; return; }
  var cards = threads[sid].cards;
  if (format === 'txt') {
    var text = 'Javis 对话导出\n' + '='.repeat(40) + '\n\n';
    cards.forEach(function(c){ text += (c.role === 'user' ? '[你] ' : '[Javis] ') + c.text + '\n' + (c.time ? '  (' + c.time + ')\n' : '') + '\n'; });
    var blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    var a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'javis_' + sid + '.txt'; a.click(); URL.revokeObjectURL(a.href);
    document.getElementById('settings-msg').textContent = '已导出 TXT';
  } else if (format === 'json') {
    var data = JSON.stringify({ id: sid, cards: cards, exported_at: new Date().toISOString() }, null, 2);
    var blob = new Blob([data], { type: 'application/json;charset=utf-8' });
    var a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'javis_' + sid + '.json'; a.click(); URL.revokeObjectURL(a.href);
    document.getElementById('settings-msg').textContent = '已导出 JSON';
  }
}

function playAudio(b64) { if (!b64) return; new Audio('data:audio/mp3;base64,' + b64).play().catch(function(){}); }

// ── Settings ──
async function showSettings() {
  try {
    var r = await fetch('/api/status'); var s = await r.json();
    document.getElementById('cfg-provider').value = s.provider;
    document.getElementById('cfg-temperature').value = s.temperature; document.getElementById('temp-val').textContent = s.temperature;
    onProviderChange(); if (s.hint) document.getElementById('settings-msg').textContent = '⚠ ' + s.hint;
  } catch(e) {}
  document.getElementById('settings-modal').style.display = 'flex';
}
function hideSettings() { document.getElementById('settings-modal').style.display = 'none'; }
function onProviderChange() { document.getElementById('apikey-group').style.display = document.getElementById('cfg-provider').value === 'local' ? 'none' : 'flex'; fetchStatus(); }
async function saveApiKey() {
  var p = document.getElementById('cfg-provider').value; var k = document.getElementById('cfg-apikey').value.trim();
  if (!k) { document.getElementById('settings-msg').textContent = '❌ 输入密钥'; return; }
  try {
    var r = await fetch('/api/config/apikey', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: p, api_key: k }) });
    var d = await r.json();
    if (d.applied) { document.getElementById('settings-msg').textContent = '已生效'; document.getElementById('cfg-apikey').value = ''; fetchStatus(); }
  } catch(e) { document.getElementById('settings-msg').textContent = '❌ ' + e.message; }
}
function onTempChange() { document.getElementById('temp-val').textContent = document.getElementById('cfg-temperature').value; }
function initProviderListener() {
  document.getElementById('cfg-provider').addEventListener('change', async function(){
    var p = document.getElementById('cfg-provider').value;
    var r = await fetch('/api/config/provider', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: p }) });
    var d = await r.json(); document.getElementById('settings-msg').textContent = d.applied ? '已切换' : '失败';
    onProviderChange(); fetchStatus();
  });
}

function escapeHtml(s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

function _saveToServer() {
  try {
    var tid = currentThreadId; var t = threads[tid];
    if (tid && t) { fetch('/api/memory/conversations/' + tid, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cards: (t.cards || []).slice(-100), name: t.name || '' }) }).catch(function(){}); }
  } catch(e) {}
}

// ── Particles ──
(function(){
  var c = document.getElementById('particles'); if (!c) return;
  var ctx = c.getContext('2d'); var w, h, ps = [];
  function rs() { w = c.width = window.innerWidth; h = c.height = window.innerHeight; }
  rs(); window.addEventListener('resize', rs);
  var colors = ['rgba(10,132,255,', 'rgba(45,212,191,', 'rgba(94,234,212,'];
  for (var i = 0; i < 25; i++) ps.push({ x: Math.random() * w, y: Math.random() * h, vx: (Math.random() - .5) * .05, vy: -(Math.random() * .08 + .02), s: Math.random() * 2 + .5, c: colors[Math.floor(Math.random() * 3)], phase: Math.random() * Math.PI * 2 });
  function dr() {
    ctx.clearRect(0, 0, w, h);
    for (var i = 0; i < ps.length; i++) {
      var p = ps[i];
      p.x += p.vx + Math.sin(p.phase) * .03; p.y += p.vy; p.phase += .008;
      if (p.y < -10) { p.y = h + 10; p.x = Math.random() * w; } if (p.x < -10) p.x = w; if (p.x > w + 10) p.x = 0;
      var g = ctx.createRadialGradient(p.x - p.s * .3, p.y - p.s * .3, 0, p.x, p.y, p.s * 5);
      g.addColorStop(0, p.c + '.35)'); g.addColorStop(.3, p.c + '.10)'); g.addColorStop(1, p.c + '0)');
      ctx.beginPath(); ctx.arc(p.x, p.y, p.s * 5, 0, Math.PI * 2); ctx.fillStyle = g; ctx.fill();
      ctx.beginPath(); ctx.arc(p.x - p.s * .4, p.y - p.s * .4, p.s * .6, 0, Math.PI * 2); ctx.fillStyle = 'rgba(255,255,255,.25)'; ctx.fill();
    }
    requestAnimationFrame(dr);
  }
  dr();
})();

document.addEventListener('DOMContentLoaded', init);

// ═══════════════════════════════════════════════════════════════
// WORKSPACE — 文件/终端/浏览器/GitHub/项目
// ═══════════════════════════════════════════════════════════════

let wsTabHistory = [];  // 浏览器历史
let wsBrowserIdx = -1;
let wsEditorPath = '';  // 当前编辑的文件路径

function toggleWorkspace() {
  var p = document.getElementById('workspace-panel');
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
  var activeTab = document.querySelector('.ws-tab[data-tab="' + tab + '"]');
  if (activeTab) activeTab.classList.add('active');
  var activeContent = document.getElementById('ws-' + tab);
  if (activeContent) activeContent.classList.add('active');
  // Lazy load
  if (tab === 'files' && !document.querySelector('#ws-files .ws-file-item')) wsExplore('.');
  if (tab === 'github') wsGitHubStatus();
  if (tab === 'project') wsListProjects();
}

// ── File Browser ──

function wsExplore(path) {
  var list = document.getElementById('ws-file-list');
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
          var item = document.createElement('div');
          item.className = 'ws-file-item';
          item.innerHTML = '<span class="ws-ficon">📁</span><span class="ws-fname" style="color:var(--accent)">' + escapeHtml(q.name) + '</span>';
          item.onclick = function(){ wsExplore(q.path); };
          list.appendChild(item);
        });
      }
      d.entries.forEach(function(e){
        var item = document.createElement('div');
        item.className = 'ws-file-item';
        var icon = e.is_dir ? '📁' : (e.name.match(/\.(py|js|ts|rs|go)$/) ? '📄' : (e.name.match(/\.(jpg|png|svg|ico)$/) ? '🖼️' : (e.name.match(/\.(md|txt)$/) ? '📝' : (e.name.match(/\.(json|yaml|yml|toml)$/) ? '⚙️' : '📄'))));
        item.innerHTML = '<span class="ws-ficon">' + icon + '</span><span class="ws-fname">' + escapeHtml(e.name) + '</span><span class="ws-fsize">' + (e.is_dir ? '' : (e.size > 1024 ? Math.round(e.size/1024) + 'KB' : e.size + 'B')) + '</span>';
        item.onclick = function(){ if(e.is_dir) wsExplore(e.path); else wsOpenFile(e.path); };
        list.appendChild(item);
      });
    })
    .catch(function(err){ list.innerHTML = '<div class="ws-loading" style="color:var(--red)">请求失败: ' + err.message + '</div>'; });
}

function wsGoUp() {
  var p = document.getElementById('ws-file-path').value.replace(/\\/g, '/').replace(/\/$/, '');
  var up = p.lastIndexOf('/');
  wsExplore(up > 0 ? p.slice(0, up) : '.');
}

function wsQuickDir(dir) {
  wsExplore(dir || '.');
}

function wsOpenFile(path) {
  document.getElementById('ws-editor-name').textContent = path;
  document.getElementById('ws-file-editor').style.display = 'flex';
  var editor = document.getElementById('ws-editor-content');
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
  var content = document.getElementById('ws-editor-content').value;
  fetch('/api/workspace/save', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({path: wsEditorPath, content: content})
  })
  .then(function(r){ return r.json(); })
  .then(function(d){
    if (d.ok) {
      var hn = document.querySelector('.ws-editor-name');
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
  var input = document.getElementById('ws-term-input');
  var output = document.getElementById('ws-terminal-output');
  var shell = document.getElementById('ws-shell-select').value;
  var cmd = input.value.trim();
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
  var url = document.getElementById('ws-browser-url').value.trim();
  if (!url.startsWith('http://') && !url.startsWith('https://')) url = 'https://' + url;
  document.getElementById('ws-browser-url').value = url;
  var iframe = document.getElementById('ws-browser-iframe');
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
  var iframe = document.getElementById('ws-browser-iframe');
  if (iframe) try { iframe.src = iframe.src; } catch(e) {}
}

// ── GitHub ──

function wsGitHubStatus() {
  fetch('/api/workspace/github')
    .then(function(r){ return r.json(); })
    .then(function(d){
      var el = document.getElementById('ws-github-status');
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
  var result = document.getElementById('ws-gh-result');
  if (!result) return;
  var cmd = '';
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
  var name = document.getElementById('ws-proj-name').value.trim();
  var type = document.getElementById('ws-proj-type').value;
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
  var src = document.getElementById('ws-proj-source').value.trim();
  if (!src) { alert('输入源文件夹路径'); return; }
  var parts = src.replace(/\\/g, '/').split('/');
  var folderName = parts[parts.length - 1] || 'imported';
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
    var list = document.getElementById('ws-proj-list');
    if (!list) return;
    if (d.ok && d.projects && d.projects.length) {
      list.innerHTML = '';
      d.projects.forEach(function(p){
        var item = document.createElement('div');
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
