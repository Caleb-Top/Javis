(function (global) {
  'use strict';

  var TERMINALS = {
    'request.completed': 'completed',
    'request.cancelled': 'cancelled',
    'request.failed': 'failed'
  };
  var LABELS = {
    understanding: '正在理解请求',
    planning: '正在规划',
    tool_started: '正在使用工具',
    tool_completed: '工具执行完成',
    verifying: '正在验证结果',
    fallback: '正在尝试替代方案'
  };

  function initialState(requestId, now) {
    var timestamp = Number(now == null ? Date.now() : now);
    return {
      requestId: String(requestId || ''),
      lastSequence: 0,
      current: '正在理解请求',
      items: [],
      terminal: null,
      collapsed: false,
      startedAt: timestamp,
      updatedAt: timestamp
    };
  }

  function cleanText(value, fallback) {
    var text = typeof value === 'string' ? value : '';
    text = text.replace(/(api[_-]?key|token|password|secret)\s*[:=]\s*\S+/gi, '$1=[redacted]');
    text = text.replace(/[\r\n\t]+/g, ' ').replace(/\s{2,}/g, ' ').trim();
    if (!text) text = fallback || '';
    return text.slice(0, 160);
  }

  function activitySummary(event) {
    var type = String(event.type || '');
    var payload = event.payload && typeof event.payload === 'object' ? event.payload : {};
    if (type === 'approval.required') return cleanText(payload.reason, '等待你的确认');
    if (type === 'activity.tool_started') return '使用 ' + cleanText(payload.tool, '工具');
    if (type === 'activity.tool_completed') {
      return payload.success === false ? '工具执行失败' : '工具执行完成';
    }
    if (type.indexOf('activity.') === 0) {
      var activity = type.slice('activity.'.length);
      return cleanText(payload.detail, LABELS[activity] || '正在处理');
    }
    return '';
  }

  function reduce(state, event) {
    if (!state || !event || String(event.request_id || '') !== state.requestId) return state;
    var sequence = Number(event.sequence || 0);
    if (sequence && sequence <= state.lastSequence) return state;
    var next = {
      requestId: state.requestId,
      lastSequence: sequence || state.lastSequence,
      current: state.current,
      items: state.items.slice(),
      terminal: state.terminal,
      collapsed: state.collapsed,
      startedAt: state.startedAt,
      updatedAt: Number(event.timestamp || Date.now())
    };
    if (event.type === 'request.accepted') {
      next.current = '正在理解请求';
      next.terminal = null;
      next.collapsed = false;
      return next;
    }
    var summary = activitySummary(event);
    if (summary) {
      next.current = summary;
      next.items.push({
        sequence: next.lastSequence,
        type: String(event.type),
        text: summary,
        status: event.type === 'activity.tool_completed' && event.payload && event.payload.success === false
          ? 'failed'
          : 'complete'
      });
    }
    var terminal = TERMINALS[event.type];
    if (terminal) {
      next.terminal = terminal;
      next.collapsed = true;
      next.current = terminal === 'completed' ? '已完成'
        : terminal === 'cancelled' ? '已中断'
          : '执行失败';
    }
    return next;
  }

  function mount(root, options) {
    options = options || {};
    var records = new Map();

    function createRecord(requestId) {
      var card = document.createElement('section');
      card.className = 'conversation-activity';
      card.dataset.requestId = requestId;
      card.innerHTML =
        '<div class="activity-summary">' +
          '<span class="activity-pulse" aria-hidden="true"></span>' +
          '<button class="activity-disclosure" type="button" aria-expanded="true">正在理解请求</button>' +
          '<time class="activity-elapsed">0s</time>' +
          '<button class="activity-stop" type="button" title="停止当前任务" aria-label="停止当前任务">■</button>' +
        '</div>' +
        '<ol class="activity-timeline"></ol>';
      root.appendChild(card);
      var record = { card: card, state: initialState(requestId) };
      records.set(requestId, record);
      card.querySelector('.activity-disclosure').addEventListener('click', function () {
        record.state = Object.assign({}, record.state, { collapsed: !record.state.collapsed });
        render(record);
      });
      card.querySelector('.activity-stop').addEventListener('click', function () {
        if (typeof options.onCancel === 'function') options.onCancel(requestId);
      });
      return record;
    }

    function render(record) {
      var state = record.state;
      var card = record.card;
      card.dataset.terminal = state.terminal || 'active';
      card.classList.toggle('collapsed', Boolean(state.collapsed));
      var disclosure = card.querySelector('.activity-disclosure');
      disclosure.textContent = state.current;
      disclosure.setAttribute('aria-expanded', String(!state.collapsed));
      var elapsed = Math.max(0, Date.now() - state.startedAt);
      card.querySelector('.activity-elapsed').textContent = elapsed < 1000 ? '<1s' : Math.round(elapsed / 1000) + 's';
      card.querySelector('.activity-stop').hidden = Boolean(state.terminal);
      var list = card.querySelector('.activity-timeline');
      list.replaceChildren();
      state.items.forEach(function (item) {
        var row = document.createElement('li');
        row.dataset.status = item.status;
        row.textContent = item.text;
        list.appendChild(row);
      });
      root.scrollTop = root.scrollHeight;
    }

    function begin(requestId) {
      var id = String(requestId || '');
      if (!id) return null;
      var record = records.get(id) || createRecord(id);
      render(record);
      return record.state;
    }

    function accept(event) {
      var requestId = String(event && event.request_id || '');
      if (!requestId) return null;
      var record = records.get(requestId);
      if (!record && event.type === 'request.accepted') record = createRecord(requestId);
      if (!record) return null;
      record.state = reduce(record.state, event);
      render(record);
      return record.state;
    }

    function terminal(requestId, type) {
      return accept({ type: type, request_id: requestId, sequence: 0, timestamp: Date.now(), payload: {} });
    }

    return {
      begin: begin,
      accept: accept,
      complete: function (id) { return terminal(id, 'request.completed'); },
      cancel: function (id) { return terminal(id, 'request.cancelled'); },
      fail: function (id) { return terminal(id, 'request.failed'); },
      dispose: function () { records.clear(); }
    };
  }

  global.JavisConversationActivity = Object.freeze({
    initialState: initialState,
    reduce: reduce,
    mount: mount
  });
})(globalThis);
