/* ==========================================================================
   SciForge Console — vanilla JS（无框架、无构建、无 CDN）
   页面：对话 / 文献 / 服务器 / 实验 / 图表 / 技能 / 关于
   ========================================================================== */
(function () {
  'use strict';

  /* ---------------------------------- 基础工具 ---------------------------------- */
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  const state = {
    health: null,
    page: 'chat',
    mock: true,
    chat: {
      attachments: [],
      sessionId: null,
      streaming: false,
      controller: null,
      assistant: null,
      pendingTools: [],
      buffer: '',
    },
    lit: { tab: 'search', current: [], daily: null, library: null, loaded: false, configLoaded: false },
    servers: [],
    llm: { loaded: false, providers: [], roles: {}, default: '' },
    gpuProbed: false,
    pipe: { runId: null, timer: null, last: null },
    metrics: { timer: null, data: null },
    artifactsLoaded: false,
    skillsLoaded: false,
  };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function escapeHtml(value) {
    return String(value === null || value === undefined ? '' : value).replace(/[&<>"']/g, (ch) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[ch]));
  }

  function prettyJson(value) {
    try {
      return JSON.stringify(value, null, 2);
    } catch (err) {
      return String(value);
    }
  }

  function fmtBytes(size) {
    const n = Number(size) || 0;
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    return (n / (1024 * 1024)).toFixed(1) + ' MB';
  }

  function fmtDuration(seconds) {
    const n = Number(seconds);
    if (!Number.isFinite(n)) return '—';
    if (n < 60) return n.toFixed(1) + 's';
    const m = Math.floor(n / 60);
    const s = Math.round(n % 60);
    return m + 'm ' + s + 's';
  }

  function fmtElapsed(seconds) {
    const n = Number(seconds);
    if (!Number.isFinite(n)) return '—';
    return n < 0.1 ? Math.max(1, Math.round(n * 1000)) + 'ms' : fmtDuration(n);
  }

  function num(value) {
    if (value === null || value === undefined) return null;
    const match = String(value).match(/-?\d+(\.\d+)?/);
    return match ? Number(match[0]) : null;
  }

  function shortText(value, max) {
    const text = typeof value === 'string' ? value : prettyJson(value);
    const flat = text.replace(/\s+/g, ' ').trim();
    return flat.length > (max || 80) ? flat.slice(0, (max || 80) - 1) + '…' : flat;
  }

  async function api(path, options) {
    const opts = Object.assign({ headers: {} }, options || {});
    const resp = await fetch(path, opts);
    let data = null;
    try {
      data = await resp.json();
    } catch (err) { /* 非 JSON 响应 */ }
    if (!resp.ok) {
      const detail = data && (data.detail || data.error) ? (data.detail || data.error) : 'HTTP ' + resp.status;
      throw new Error(typeof detail === 'string' ? detail : prettyJson(detail));
    }
    return data;
  }

  function apiPost(path, body) {
    return api(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
  }

  async function fetchText(path) {
    const resp = await fetch(path);
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    return resp.text();
  }

  function toast(message, kind, timeout) {
    const box = $('#toasts');
    const node = el('div', 'toast ' + (kind || 'info'), message);
    box.appendChild(node);
    setTimeout(() => {
      node.style.opacity = '0';
      node.style.transform = 'translateY(8px)';
      setTimeout(() => node.remove(), 260);
    }, timeout || 4600);
  }

  function emptyState(icon, message) {
    return '<div class="empty"><span class="empty-icon">' + icon + '</span><p>' + escapeHtml(message) + '</p></div>';
  }

  function noticeHtml(message, kind) {
    return '<div class="notice ' + (kind || '') + '">' + escapeHtml(message) + '</div>';
  }

  /* ---------------------------------- Markdown ---------------------------------- */
  function inlineMd(text) {
    let out = escapeHtml(text);
    out = out.replace(/`([^`]+)`/g, '<code>$1</code>');
    out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    out = out.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    return out;
  }

  function splitTableRow(line) {
    let row = String(line).trim();
    if (row.startsWith('|')) row = row.slice(1);
    if (row.endsWith('|')) row = row.slice(0, -1);
    return row.split('|').map((cell) => cell.trim());
  }

  function isTableSeparator(line) {
    return /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{1,}:?\s*)+\|?\s*$/.test(String(line));
  }

  function renderMarkdown(text) {
    if (!text) return '';
    let src = String(text).replace(/\r\n/g, '\n');
    const codeBlocks = [];
    src = src.replace(/```([\w+-]*)[ \t]*\n([\s\S]*?)```/g, (match, lang, code) => {
      codeBlocks.push('<pre><code data-lang="' + escapeHtml(lang || '') + '">' + escapeHtml(code.replace(/\n$/, '')) + '</code></pre>');
      return '\u0000BLOCK' + (codeBlocks.length - 1) + '\u0000';
    });

    const lines = src.split('\n');
    const out = [];
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];
      const trimmed = line.trim();

      const blockMatch = trimmed.match(/^\u0000BLOCK(\d+)\u0000$/);
      if (blockMatch) {
        out.push(codeBlocks[Number(blockMatch[1])]);
        i += 1;
        continue;
      }
      if (!trimmed) { i += 1; continue; }

      const heading = trimmed.match(/^(#{1,6})\s+(.*)$/);
      if (heading) {
        const level = Math.min(heading[1].length, 4);
        out.push('<h' + level + '>' + inlineMd(heading[2]) + '</h' + level + '>');
        i += 1;
        continue;
      }

      if (/^>\s?/.test(trimmed)) {
        const quote = [];
        while (i < lines.length && /^>\s?/.test(lines[i].trim())) {
          quote.push(lines[i].trim().replace(/^>\s?/, ''));
          i += 1;
        }
        out.push('<blockquote>' + inlineMd(quote.join(' ')) + '</blockquote>');
        continue;
      }

      if (trimmed.includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
        const header = splitTableRow(trimmed);
        i += 2;
        const rows = [];
        while (i < lines.length && lines[i].trim() && lines[i].includes('|')) {
          rows.push(splitTableRow(lines[i]));
          i += 1;
        }
        out.push(
          '<table><thead><tr>' + header.map((cell) => '<th>' + inlineMd(cell) + '</th>').join('') + '</tr></thead><tbody>' +
          rows.map((row) => '<tr>' + header.map((cell, idx) => '<td>' + inlineMd(row[idx] || '') + '</td>').join('') + '</tr>').join('') +
          '</tbody></table>'
        );
        continue;
      }

      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const bullet = /^\s*[-*+]\s+/.test(line);
      if (ordered || bullet) {
        const items = [];
        const pattern = ordered ? /^\s*\d+[.)]\s+(.*)$/ : /^\s*[-*+]\s+(.*)$/;
        while (i < lines.length) {
          const itemMatch = lines[i].match(pattern);
          if (itemMatch) {
            items.push(inlineMd(itemMatch[1]));
            i += 1;
          } else if (items.length && lines[i].trim() && /^\s{2,}\S/.test(lines[i])) {
            items[items.length - 1] += ' ' + inlineMd(lines[i].trim());
            i += 1;
          } else {
            break;
          }
        }
        out.push(ordered
          ? '<ol>' + items.map((item) => '<li>' + item + '</li>').join('') + '</ol>'
          : '<ul>' + items.map((item) => '<li>' + item + '</li>').join('') + '</ul>');
        continue;
      }

      const paragraph = [trimmed];
      i += 1;
      while (i < lines.length) {
        const next = lines[i];
        const nextTrimmed = next.trim();
        if (!nextTrimmed) break;
        if (/^(#{1,6}\s|>\s?|\u0000BLOCK)/.test(nextTrimmed)) break;
        if (/^\s*([-*+]|\d+[.)])\s+/.test(next)) break;
        if (nextTrimmed.includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1])) break;
        paragraph.push(nextTrimmed);
        i += 1;
      }
      out.push('<p>' + inlineMd(paragraph.join(' ')) + '</p>');
    }
    return out.join('\n');
  }

  /* ---------------------------------- 主题 ---------------------------------- */
  function applyTheme(theme) {
    const next = theme === 'light' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('sciforge-theme', next); } catch (err) { /* ignore */ }
    const btn = $('#theme-toggle');
    if (btn) {
      const sun = '<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">' +
        '<circle cx="10" cy="10" r="3.3" fill="none" stroke="currentColor" stroke-width="1.5"/>' +
        '<path d="M10 2.1v2.1M10 15.8v2.1M2.1 10h2.1M15.8 10h2.1M4.4 4.4l1.5 1.5M14.1 14.1l1.5 1.5M15.6 4.4l-1.5 1.5M5.9 14.1l-1.5 1.5" ' +
        'stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>';
      const moon = '<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">' +
        '<path d="M16.2 12.4A7 7 0 0 1 7.6 3.8a7.2 7.2 0 1 0 8.6 8.6Z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>';
      btn.innerHTML = next === 'dark' ? sun : moon;
      btn.title = next === 'dark' ? '切换到浅色主题' : '切换到深色主题';
    }
  }

  /* ---------------------------------- 导航 ---------------------------------- */
  function pageFromHash() {
    const match = (window.location.hash || '').match(/^#\/?([a-z-]+)/);
    if (!match) return null;
    return document.getElementById('page-' + match[1]) ? match[1] : null;
  }

  function setPage(page) {
    state.page = page;
    $$('.nav-item').forEach((item) => item.classList.toggle('active', item.dataset.page === page));
    $$('.page').forEach((section) => section.classList.toggle('active', section.id === 'page-' + page));
    const active = $('#page-' + page);
    if (active) {
      $('#page-title').textContent = active.dataset.title || '';
      $('#page-subtitle').textContent = active.dataset.subtitle || '';
    }
    if (page === 'literature') {
      if (!state.lit.loaded) loadLiterature();
      if (!state.lit.configLoaded) loadLitConfig();
    }
    if (page === 'llm') loadLlmPage();
    if (page === 'servers') loadServersPage();
    if (page === 'experiments') loadExperimentsPage();
    if (page !== 'experiments') stopMetricsAuto();
    if (page === 'figures' && !state.artifactsLoaded) loadArtifacts();
    if (page === 'skills' && !state.skillsLoaded) loadSkills('');
    if (page === 'about') renderAbout();
    if (window.location.hash !== '#/' + page) {
      try { history.replaceState(null, '', '#/' + page); } catch (err) { /* ignore */ }
    }
  }

  /* ---------------------------------- 健康检查 / 关于 ---------------------------------- */
  async function loadHealth() {
    try {
      const health = await api('/api/health');
      state.health = health;
      state.mock = Boolean(health.mock);

      const badge = $('#health-badge');
      badge.textContent = health.mock ? '离线演示模式' : 'LLM 已连接';
      badge.className = 'badge ' + (health.mock ? 'badge-warn' : 'badge-ok');

      const llm = health.llm || {};
      const llmDot = $('#dot-llm');
      const llmLabel = $('#llm-label');
      if (health.mock) {
        llmDot.className = 'dot dot-warn';
        llmLabel.textContent = 'Mock LLM · 离线可用';
      } else if (llm.ok) {
        llmDot.className = 'dot dot-ok';
        llmLabel.textContent = (llm.provider || 'llm') + ' / ' + (llm.model || '');
      } else {
        llmDot.className = 'dot dot-err';
        llmLabel.textContent = 'LLM 未配置（可离线 Mock）';
      }

      const tools = health.tools || {};
      const toolDot = $('#dot-tools');
      toolDot.className = 'dot ' + (tools.count > 0 ? 'dot-ok' : 'dot-warn');
      $('#tools-label').textContent = (tools.count || 0) + ' 个工具已注册';

      if (!state.chat.sessionId) {
        $('#mock-toggle').checked = state.mock;
        $('#pipe-mock').checked = state.mock;
      }
      renderAbout();
    } catch (err) {
      $('#health-badge').textContent = '服务不可用';
      $('#health-badge').className = 'badge badge-err';
      $('#dot-llm').className = 'dot dot-err';
      $('#llm-label').textContent = '无法连接 /api/health';
      toast('无法连接后端：' + err.message, 'err', 8000);
    }
  }

  function renderAbout() {
    const health = state.health;
    if (!health) return;
    $('#about-version').textContent = 'v' + (health.version || '?');
    const llm = health.llm || {};
    const tools = health.tools || {};
    const items = [
      ['项目目录', health.project && health.project.dir],
      ['LLM Provider', (llm.provider || '—') + ' / ' + (llm.model || '—')],
      ['API Key', llm.ok ? ('已配置（来源 ' + (llm.api_key_source || 'env') + '）') : '未配置（离线 Mock）'],
      ['Mock 模式', health.mock ? '开（默认不调用真实 API）' : '关'],
      ['工具数量', (tools.count || 0) + ' 个'],
      ['工具 import 错误', Object.keys(tools.import_errors || {}).length ? JSON.stringify(tools.import_errors) : '无'],
      ['服务器', ((health.servers || {}).names || []).join(', ') || '未配置'],
      ['Fixtures', health.fixtures && health.fixtures.available ? health.fixtures.dir : '未找到'],
      ['运行时长', (health.web && health.web.uptime) ? fmtDuration(health.web.uptime) : '—'],
      ['启动时间', health.time || '—'],
    ];
    $('#about-health').innerHTML = items.map((pair) =>
      '<div class="kv-item"><span class="kv-key">' + escapeHtml(pair[0]) + '</span><span class="kv-val">' + escapeHtml(pair[1] || '—') + '</span></div>'
    ).join('');

    const features = health.features || {};
    const labels = {
      library: 'sciforge.library（T1 文献库）',
      skills_manager: 'sciforge.skills_manager（T4 技能）',
      pipeline: 'sciforge.pipeline.engine（流水线）',
      figures: 'sciforge.figures（T3 配图）',
      writing: 'sciforge.writing（T3 写作）',
      remote: 'sciforge.tools.remote（T2 远程 GPU）',
    };
    $('#about-features').innerHTML = Object.keys(labels).map((key) => {
      const ready = Boolean(features[key]);
      return '<div class="feature-row"><span class="dot ' + (ready ? 'dot-ok' : 'dot-warn') + '"></span>' +
        '<span class="feature-name">' + escapeHtml(labels[key]) + '</span>' +
        '<span class="feature-state ' + (ready ? 'ready' : 'pending') + '">' + (ready ? '已就绪' : '未就绪（优雅降级）') + '</span></div>';
    }).join('');
  }

  /* ---------------------------------- 对话 ---------------------------------- */
  let chatToolsById = new Map();

  function scrollChat(force) {
    const box = $('#chat-messages');
    if (!box) return;
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 180;
    if (force || nearBottom) box.scrollTop = box.scrollHeight;
  }

  function hideWelcome() {
    const welcome = $('#chat-welcome');
    if (welcome) welcome.remove();
  }

  function addUserMessage(text, attachments) {
    hideWelcome();
    const wrap = el('div', 'msg user');
    wrap.innerHTML =
      '<div class="msg-avatar">你</div>' +
      '<div class="msg-body"><div class="msg-meta"><span class="msg-role">You</span></div>' +
      '<div class="msg-content"></div></div>';
    wrap.querySelector('.msg-content').textContent = text;
    const chips = Array.isArray(attachments) ? attachments : [];
    if (chips.length) {
      const bar = el('div', 'attach-bar');
      chips.forEach((item) => {
        const chip = el('span', 'attach-chip', '📎 ' + (item.name || 'file'));
        bar.appendChild(chip);
      });
      wrap.querySelector('.msg-body').appendChild(bar);
    }
    $('#chat-messages').appendChild(wrap);
    scrollChat(true);
  }

  /* ---------- 对话附件上传 ---------- */
  function renderChatAttachments() {
    const bar = $('#chat-attachments');
    if (!bar) return;
    if (!state.chat.attachments.length) {
      bar.classList.add('hidden');
      bar.innerHTML = '';
      return;
    }
    bar.classList.remove('hidden');
    bar.innerHTML = state.chat.attachments.map((item, index) =>
      '<span class="attach-chip"><span class="ac-name">📎 ' + escapeHtml(item.name || 'file') + '</span>' +
      '<span class="muted">' + fmtBytes(item.size || 0) + '</span>' +
      '<button type="button" data-remove-attach="' + index + '" title="移除">✕</button></span>'
    ).join('');
  }

  async function uploadChatFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    toast('正在上传 ' + files.length + ' 个文件…', 'info', 3000);
    const form = new FormData();
    files.forEach((file) => form.append('files', file));
    try {
      const resp = await fetch('/api/upload', { method: 'POST', body: form });
      const data = await resp.json().catch(() => null);
      if (!resp.ok || !data || !data.ok) {
        throw new Error((data && (data.detail || data.error)) || ('HTTP ' + resp.status));
      }
      (data.files || []).forEach((item) => state.chat.attachments.push(item));
      renderChatAttachments();
      toast('已附上 ' + (data.count || 0) + ' 个文件，发送时会自动带给模型', 'ok', 4500);
    } catch (err) {
      toast('上传失败：' + err.message, 'err', 8000);
    }
  }

  function ensureAssistant() {
    if (state.chat.assistant && state.chat.assistant.wrap.isConnected) return state.chat.assistant;
    hideWelcome();
    const wrap = el('div', 'msg assistant');
    wrap.innerHTML =
      '<div class="msg-avatar">SF</div>' +
      '<div class="msg-body">' +
      '<div class="msg-meta"><span class="msg-role">SciForge</span><span class="stream-state">思考中…</span></div>' +
      '<div class="tool-cards"></div>' +
      '<div class="msg-content md typing-caret"></div>' +
      '</div>';
    $('#chat-messages').appendChild(wrap);
    state.chat.assistant = {
      wrap: wrap,
      contentEl: wrap.querySelector('.msg-content'),
      cardsEl: wrap.querySelector('.tool-cards'),
      stateEl: wrap.querySelector('.stream-state'),
      buffer: '',
    };
    scrollChat(true);
    return state.chat.assistant;
  }

  function setAssistantState(text) {
    const assistant = state.chat.assistant;
    if (assistant && assistant.stateEl) assistant.stateEl.textContent = text || '';
  }

  function renderStreamBuffer() {
    const assistant = state.chat.assistant;
    if (!assistant) return;
    assistant.buffer = state.chat.buffer;
    assistant.contentEl.innerHTML = renderMarkdown(state.chat.buffer);
    scrollChat(false);
  }

  function summarizeArgs(args) {
    if (!args || typeof args !== 'object') return '';
    const entries = Object.entries(args);
    if (!entries.length) return '（无参数）';
    return entries.slice(0, 2).map((pair) => pair[0] + '=' + shortText(pair[1], 42)).join(' · ');
  }

  function ensureToolCard(name, args) {
    const assistant = ensureAssistant();
    const card = el('div', 'tool-card');
    card.innerHTML =
      '<div class="tool-card-head">' +
      '  <span class="tool-status dot dot-live"></span>' +
      '  <span class="tool-name">' + escapeHtml(name) + '</span>' +
      '  <span class="tool-summary">' + escapeHtml(summarizeArgs(args)) + '</span>' +
      '  <span class="tool-time">运行中</span>' +
      '  <span class="tool-caret">▶</span>' +
      '</div>' +
      '<div class="tool-card-body">' +
      '  <div class="tool-section-label">参数</div>' +
      '  <pre class="tool-pre">' + escapeHtml(prettyJson(args || {})) + '</pre>' +
      '  <div class="result-slot"></div>' +
      '</div>';
    card.querySelector('.tool-card-head').addEventListener('click', () => card.classList.toggle('open'));
    assistant.cardsEl.appendChild(card);
    const entry = { name: name, card: card, done: false, started: Date.now() };
    state.chat.pendingTools.push(entry);
    chatToolsById.set(name + ':' + state.chat.pendingTools.length, entry);
    scrollChat(false);
    return entry;
  }

  function updateToolCard(payload) {
    const pending = state.chat.pendingTools;
    let entry = null;
    for (let i = pending.length - 1; i >= 0; i -= 1) {
      if (pending[i].name === payload.name && !pending[i].done) { entry = pending[i]; break; }
    }
    if (!entry) entry = ensureToolCard(payload.name, {});
    entry.done = true;
    const card = entry.card;
    card.classList.toggle('err', payload.ok === false);
    const status = card.querySelector('.tool-status');
    if (status) status.className = 'tool-status dot ' + (payload.ok === false ? 'dot-err' : 'dot-ok');
    const time = card.querySelector('.tool-time');
    if (time) {
      time.textContent = fmtElapsed(payload.elapsed);
    }
    const slot = card.querySelector('.result-slot');
    if (slot) {
      let html = '<div class="tool-section-label">结果</div>';
      if (payload.error) html += '<div class="tool-error">' + escapeHtml(payload.error) + '</div>';
      if (payload.content) html += '<pre class="tool-pre ' + (payload.ok === false ? 'tool-error' : 'ok-text') + '">' + escapeHtml(payload.content) + '</pre>';
      if (!payload.error && !payload.content) html += '<pre class="tool-pre">（空结果）</pre>';
      const artifacts = payload.artifacts || [];
      if (artifacts.length) {
        html += '<div class="tool-section-label">产物</div><div class="file-links">' +
          artifacts.map((path) => {
            const name = String(path).split('/').pop();
            return '<a class="file-link" href="/files/' + encodeURI(String(path)) + '" target="_blank" rel="noopener">📎 ' + escapeHtml(name) + '</a>' +
              '<a class="file-link" href="/api/download/' + encodeURI(String(path)) + '">⬇ 下载</a>';
          }).join('') + '</div>';
      }
      slot.innerHTML = html;
    }
    if (payload.ok === false) card.classList.add('open');
    setAssistantState('工具 ' + payload.name + (payload.ok === false ? ' 失败' : ' 完成'));
    scrollChat(false);
  }

  function addChatError(message) {
    const assistant = ensureAssistant();
    const box = el('div', 'notice err', message);
    assistant.wrap.querySelector('.msg-body').appendChild(box);
    scrollChat(true);
  }

  function renderChatStatus(parts) {
    const box = $('#chat-status');
    if (!parts || !parts.length) { box.classList.add('hidden'); box.innerHTML = ''; return; }
    box.innerHTML = parts.map((part) => '<span class="badge badge-ghost">' + escapeHtml(part) + '</span>').join('');
    box.classList.remove('hidden');
  }

  function handleAgentEvent(event, data) {
    switch (event) {
      case 'meta': {
        if (data.session_id) {
          state.chat.sessionId = data.session_id;
          $('#session-label').textContent = 'session: ' + data.session_id;
        }
        const parts = [];
        if (data.mock) parts.push('Mock LLM（离线）');
        if (data.mock_reason) parts.push(data.mock_reason);
        renderChatStatus(parts);
        break;
      }
      case 'llm_delta':
        if (data.text) {
          state.chat.buffer += data.text;
          renderStreamBuffer();
        }
        break;
      case 'assistant': {
        const assistant = ensureAssistant();
        if (typeof data.content === 'string' && data.content) {
          state.chat.buffer = data.content;
          renderStreamBuffer();
        }
        setAssistantState(data.step ? ('第 ' + data.step + ' 步') : '生成中…');
        break;
      }
      case 'tool_call':
        ensureToolCard(data.name || 'unknown', data.arguments || {});
        setAssistantState('调用工具 ' + (data.name || ''));
        break;
      case 'tool_result':
        updateToolCard(data);
        break;
      case 'stage': {
        const stage = data.stage || data.phase || '';
        if (stage === 'agent_start') setAssistantState('思考中…');
        if (stage === 'agent_end') {
          const usage = data.usage || {};
          renderChatStatus([
            '步数 ' + (data.steps || 0),
            '工具 ' + (data.tool_calls || 0) + ' 次',
            'tokens ' + (usage.total_tokens || 0),
            '停止原因 ' + (data.stopped_reason || 'stop'),
          ]);
        }
        if (stage && stage !== 'agent_start' && stage !== 'agent_end') setAssistantState(stage);
        break;
      }
      case 'error':
        addChatError(data.message || '未知错误');
        renderChatStatus(['执行出错' + (data.where ? ' @ ' + data.where : '')]);
        break;
      case 'done':
        finalizeAssistant(data);
        break;
      default:
        break;
    }
  }

  function finalizeAssistant(done) {
    const assistant = state.chat.assistant;
    if (!assistant) return;
    if (done && typeof done.content === 'string' && done.content) {
      state.chat.buffer = done.content;
      renderStreamBuffer();
    }
    assistant.contentEl.classList.remove('typing-caret');
    if (!state.chat.buffer.trim()) {
      assistant.contentEl.innerHTML = '<p class="muted">（无文本输出）</p>';
    }
    if (done && done.ok === false && done.error) addChatError(done.error);
    const usage = (done && done.usage) || {};
    renderChatStatus([
      '完成：' + ((done && done.stopped_reason) || 'stop'),
      '步数 ' + ((done && done.steps) || 0),
      '工具 ' + ((done && done.tool_calls) || 0),
      'tokens ' + (usage.total_tokens || 0),
      '耗时 ' + ((done && done.duration !== undefined) ? fmtElapsed(done.duration) : '—'),
    ]);
    if (done && done.artifacts && done.artifacts.length) {
      const box = el('div', 'file-links');
      done.artifacts.forEach((path) => {
        const link = el('a', 'file-link', '📎 ' + String(path).split('/').pop());
        link.href = '/files/' + encodeURI(String(path));
        link.target = '_blank';
        link.rel = 'noopener';
        box.appendChild(link);
      });
      assistant.wrap.querySelector('.msg-body').appendChild(box);
    }
    setAssistantState('');
  }

  function setComposerBusy(busy) {
    $('#chat-send').disabled = busy;
    $('#chat-stop').classList.toggle('hidden', !busy);
    $('#chat-input').disabled = busy;
    $('#mock-toggle').disabled = busy;
    $('#new-session').disabled = busy;
  }

  function finishStreaming() {
    state.chat.streaming = false;
    state.chat.controller = null;
    setComposerBusy(false);
    const input = $('#chat-input');
    if (input) { input.focus(); autoResizeInput(); }
  }

  function handleSSEBlock(block) {
    let event = 'message';
    const dataLines = [];
    block.split('\n').forEach((line) => {
      if (!line || line.startsWith(':')) return;
      if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''));
    });
    if (!dataLines.length) return;
    let data = null;
    try { data = JSON.parse(dataLines.join('\n')); } catch (err) { return; }
    handleAgentEvent(event, data || {});
  }

  async function sendChat(message, options) {
    const hasAttachments = state.chat.attachments.length > 0;
    const text = String(message || '').trim() || (hasAttachments ? '请阅读附件内容，总结与我研究相关的要点。' : '');
    if (!text || state.chat.streaming) return;
    const pendingAttachments = state.chat.attachments.slice();
    hideWelcome();
    addUserMessage(text, pendingAttachments);
    state.chat.attachments = [];
    renderChatAttachments();
    state.chat.buffer = '';
    state.chat.assistant = null;
    state.chat.pendingTools = [];
    chatToolsById = new Map();
    renderChatStatus([]);
    state.chat.streaming = true;
    setComposerBusy(true);
    const controller = new AbortController();
    state.chat.controller = controller;
    ensureAssistant();
    setAssistantState('连接 Agent…');

    try {
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: text,
          mock: $('#mock-toggle').checked,
          session_id: state.chat.sessionId || null,
          demo_tools: Boolean(options && options.demoTools),
          attachments: pendingAttachments.map((item) => item.rel),
        }),
        signal: controller.signal,
      });
      if (!resp.ok) {
        let detail = 'HTTP ' + resp.status;
        try {
          const data = await resp.json();
          if (data && (data.detail || data.error)) detail = data.detail || data.error;
        } catch (err) { /* ignore */ }
        throw new Error(detail);
      }
      if (!resp.body) throw new Error('当前浏览器不支持流式响应（ReadableStream）');
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let sawDone = false;
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        let index = buffer.indexOf('\n\n');
        while (index >= 0) {
          const block = buffer.slice(0, index);
          buffer = buffer.slice(index + 2);
          if (block.includes('event: done')) sawDone = true;
          handleSSEBlock(block);
          index = buffer.indexOf('\n\n');
        }
      }
      if (buffer.trim()) handleSSEBlock(buffer);
      if (!sawDone) {
        const assistant = ensureAssistant();
        assistant.contentEl.classList.remove('typing-caret');
        setAssistantState('连接已结束');
        toast('SSE 连接提前结束（未收到 done 事件），请查看上方输出', 'warn');
      }
    } catch (err) {
      if (err.name === 'AbortError') {
        const assistant = ensureAssistant();
        assistant.contentEl.classList.remove('typing-caret');
        const box = el('div', 'notice warn', '已手动停止本次生成。');
        assistant.wrap.querySelector('.msg-body').appendChild(box);
        renderChatStatus(['已停止']);
        setAssistantState('已停止');
      } else {
        addChatError('请求失败：' + err.message);
        toast(err.message, 'err', 7000);
        setAssistantState('失败');
      }
    } finally {
      finishStreaming();
    }
  }

  function resetChat() {
    if (state.chat.streaming) toast('请先停止当前生成', 'warn');
    state.chat.sessionId = null;
    state.chat.streaming = false;
    state.chat.assistant = null;
    state.chat.pendingTools = [];
    state.chat.buffer = '';
    state.chat.attachments = [];
    renderChatAttachments();
    $('#session-label').textContent = '新会话';
    renderChatStatus([]);
    $('#chat-messages').innerHTML =
      '<div class="welcome" id="chat-welcome">' +
      '<div class="welcome-mark">✦</div>' +
      '<h2>新会话已就绪</h2>' +
      '<p>继续把科研任务交给 SciForge。所有工具调用会以可折叠卡片实时展示。</p>' +
      '</div>';
    setComposerBusy(false);
    $('#chat-input').focus();
  }

  function autoResizeInput() {
    const input = $('#chat-input');
    if (!input) return;
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 190) + 'px';
  }

  /* ---------------------------------- 历史会话 ---------------------------------- */
  function addStaticAssistantMessage(text) {
    hideWelcome();
    const wrap = el('div', 'msg assistant');
    wrap.innerHTML =
      '<div class="msg-avatar">SF</div>' +
      '<div class="msg-body"><div class="msg-meta"><span class="msg-role">SciForge</span></div>' +
      '<div class="msg-content md"></div></div>';
    wrap.querySelector('.msg-content').innerHTML = renderMarkdown(text || '');
    $('#chat-messages').appendChild(wrap);
    scrollChat(true);
  }

  async function loadSessionIntoChat(sessionId) {
    try {
      const data = await api('/api/sessions/' + encodeURIComponent(sessionId));
      const box = $('#chat-messages');
      box.innerHTML = '';
      const banner = el('div', 'notice info',
        '历史会话 ' + sessionId + ' · ' + (data.created_at || '') + ' · 继续发送将接上上下文');
      box.appendChild(banner);
      state.chat.sessionId = sessionId;
      state.chat.assistant = null;
      state.chat.buffer = '';
      $('#session-label').textContent = '继续会话：' + sessionId;
      for (const message of data.messages_detail || []) {
        const role = String(message.role || '');
        const content = String(message.content || '').trim();
        if (!content) continue;
        if (role === 'user') addUserMessage(content);
        else if (role === 'assistant' && !(message.tool_calls || []).length) addStaticAssistantMessage(content);
      }
      toast('已载入历史会话，可继续对话', 'ok', 4000);
    } catch (err) {
      toast('载入历史会话失败：' + err.message, 'err', 8000);
    }
  }

  async function showSessionHistory() {
    try {
      const data = await api('/api/sessions?limit=80');
      const list = el('div', 'files-list');
      const sessions = data.sessions || [];
      if (!sessions.length) {
        list.innerHTML = emptyState('🗂️', '还没有历史会话。完成一次对话后会自动保存在 .sciforge/sessions/。');
      } else {
        sessions.forEach((item) => {
          const row = el('div', 'file-row');
          row.innerHTML =
            '<div class="file-icon">' + (item.source === 'web' ? '💬' : '🧪') + '</div>' +
            '<div class="file-main">' +
            '<span class="file-name">' + escapeHtml(item.session_id) + '</span>' +
            '<span class="file-sub">' + escapeHtml(item.created_at || '') + ' · ' +
            (item.messages || 0) + ' 条消息 · ' + (item.tool_calls || 0) + ' 次工具调用</span>' +
            '<span class="file-sub">' + escapeHtml(item.preview || item.task || '') + '</span>' +
            '</div>';
          row.addEventListener('click', () => {
            closeModal();
            loadSessionIntoChat(item.session_id);
          });
          list.appendChild(row);
        });
      }
      const tip = el('p', 'muted', '点击任意会话可在对话区恢复查看；继续发送消息会接上该会话上下文。');
      const wrap = el('div', '');
      wrap.appendChild(tip);
      wrap.appendChild(list);
      openModal('历史会话', wrap);
    } catch (err) {
      toast('读取历史会话失败：' + err.message, 'err', 8000);
    }
  }

  function bindChat() {
    const form = $('#chat-form');
    const input = $('#chat-input');
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const text = input.value;
      input.value = '';
      autoResizeInput();
      sendChat(text);
    });
    input.addEventListener('input', autoResizeInput);
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        form.requestSubmit();
      }
    });
    $('#chat-stop').addEventListener('click', () => {
      if (state.chat.controller) state.chat.controller.abort();
    });
    $('#chat-file-input').addEventListener('change', (event) => {
      uploadChatFiles(event.target.files);
      event.target.value = '';
    });
    $('#chat-attachments').addEventListener('click', (event) => {
      const btn = event.target.closest('button[data-remove-attach]');
      if (!btn) return;
      state.chat.attachments.splice(Number(btn.dataset.removeAttach), 1);
      renderChatAttachments();
    });
    form.addEventListener('dragover', (event) => {
      event.preventDefault();
      form.classList.add('dragging');
    });
    form.addEventListener('dragleave', () => form.classList.remove('dragging'));
    form.addEventListener('drop', (event) => {
      event.preventDefault();
      form.classList.remove('dragging');
      if (event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files.length) {
        uploadChatFiles(event.dataTransfer.files);
      }
    });
    $('#new-session').addEventListener('click', resetChat);
    $('#history-btn').addEventListener('click', showSessionHistory);
    document.querySelectorAll('#quick-chips .chip').forEach((chip) => {
      chip.addEventListener('click', () => {
        const prompt = chip.dataset.prompt || chip.textContent;
        input.value = '';
        autoResizeInput();
        sendChat(prompt, { demoTools: chip.dataset.demo === 'tools' });
      });
    });
    $('#mock-toggle').addEventListener('change', (event) => {
      state.mock = event.target.checked;
      toast(event.target.checked ? '已切换到离线 Mock 模式' : '已切换到真实 LLM（需配置 API key）', 'info', 3000);
    });
  }

  /* ---------------------------------- 文献 ---------------------------------- */
  function paperCard(paper, index, context) {
    const authors = (paper.authors || []).join(', ');
    const summary = paper.summary_zh || (paper.summary && paper.summary !== paper.abstract ? paper.summary : '');
    const tags = (paper.categories || []).slice(0, 3).map((tag) => '<span class="tag-chip">' + escapeHtml(tag) + '</span>').join('');
    return '' +
      '<article class="paper-card" data-idx="' + index + '">' +
      '<div class="paper-title">' + escapeHtml(paper.title || '(无标题)') + '</div>' +
      '<div class="paper-meta">' +
      '<span>' + escapeHtml(paper.id || '') + '</span>' +
      (paper.published ? '<span>' + escapeHtml(paper.published) + '</span>' : '') +
      (paper.source ? '<span class="badge badge-ghost">' + escapeHtml(paper.source) + '</span>' : '') +
      '</div>' +
      (authors ? '<div class="paper-authors" title="' + escapeHtml(authors) + '">' + escapeHtml(authors) + '</div>' : '') +
      (summary ? '<div class="paper-summary">' + escapeHtml(summary) + '</div>' : '') +
      (!summary && paper.abstract ? '<div class="paper-abstract">' + escapeHtml(paper.abstract) + '</div>' : '') +
      (tags ? '<div class="tag-list">' + tags + '</div>' : '') +
      '<div class="paper-foot">' +
      '<button type="button" class="btn btn-primary btn-sm" data-act="read" data-ctx="' + context + '" data-idx="' + index + '">精读</button>' +
      '<button type="button" class="btn btn-ghost btn-sm" data-act="add" data-ctx="' + context + '" data-idx="' + index + '">加入库</button>' +
      (paper.url ? '<a class="btn btn-ghost btn-sm" href="' + escapeHtml(paper.url) + '" target="_blank" rel="noopener">原文 ↗</a>' : '') +
      (paper.pdf_url ? '<a class="btn btn-ghost btn-sm" href="' + escapeHtml(paper.pdf_url) + '" target="_blank" rel="noopener">PDF ↗</a>' : '') +
      '</div></article>';
  }

  function renderPaperList(container, papers, context, emptyMessage) {
    if (!papers || !papers.length) {
      container.innerHTML = emptyState('📭', emptyMessage || '没有结果');
      return;
    }
    container.innerHTML = papers.map((paper, index) => paperCard(paper, index, context)).join('');
  }

  async function runLiteratureSearch(query) {
    state.lit.tab = 'search';
    $$('#lit-tabs .tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.tab === 'search'));
    const box = $('#lit-results');
    box.innerHTML = emptyState('🔎', '检索中…');
    $('#lit-notice').classList.add('hidden');
    try {
      const source = $('#lit-source').value || 'arxiv';
      const publisher = source === 'crossref' ? ($('#lit-publisher').value || '') : '';
      const data = await api('/api/literature/search?query=' + encodeURIComponent(query || '') +
        '&limit=30&source=' + encodeURIComponent(source) +
        (publisher ? '&publisher=' + encodeURIComponent(publisher) : ''));
      state.lit.current = data.papers || [];
      state.lit.loaded = true;
      const badge = $('#lit-source-badge');
      badge.textContent = data.degraded ? '离线 Demo Fixtures' : 'lit_search · ' + (data.search_source || data.source || 'arxiv');
      badge.className = 'badge ' + (data.degraded ? 'badge-warn' : 'badge-ok');
      if (data.notice) {
        $('#lit-notice').innerHTML = noticeHtml(data.notice, data.degraded ? 'warn' : '');
        $('#lit-notice').classList.remove('hidden');
      }
      const papers = (data.papers || []).filter((paper) => paper.source !== 'daily');
      const daily = (data.papers || []).filter((paper) => paper.source === 'daily');
      state.lit.daily = daily;
      renderPaperList(box, papers.length ? papers : (data.papers || []), 'search', query ? '没有匹配的论文，试试更宽泛的关键词' : '暂无文献数据');
    } catch (err) {
      box.innerHTML = emptyState('⚠️', '检索失败：' + err.message);
      $('#lit-source-badge').textContent = '检索失败';
      $('#lit-source-badge').className = 'badge badge-err';
    }
  }

  async function loadDaily() {
    state.lit.tab = 'daily';
    $$('#lit-tabs .tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.tab === 'daily'));
    const box = $('#lit-results');
    box.innerHTML = emptyState('📰', '加载每日 arXiv 速览…');
    try {
      let daily = state.lit.daily;
      if (!daily || !daily.length) {
        const data = await api('/api/literature/search?query=&limit=50');
        state.lit.loaded = true;
        daily = (data.papers || []).filter((paper) => paper.source === 'daily');
      }
      state.lit.current = daily || [];
      $('#lit-source-badge').textContent = 'daily-arXiv-ai-enhanced';
      $('#lit-source-badge').className = 'badge badge-accent';
      $('#lit-notice').innerHTML = noticeHtml('字段对齐 daily-arXiv-ai-enhanced：AI_summary / AI_keywords 中文增强摘要，离线 fixtures 中提供 3 条真实格式示例。');
      $('#lit-notice').classList.remove('hidden');
      renderPaperList(box, daily || [], 'daily', 'daily fixtures 为空，请检查 examples/demo/fixtures/daily_arxiv.jsonl');
    } catch (err) {
      box.innerHTML = emptyState('⚠️', '加载失败：' + err.message);
    }
  }

  async function loadLibrary() {
    state.lit.tab = 'library';
    $$('#lit-tabs .tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.tab === 'library'));
    const box = $('#lit-results');
    box.innerHTML = emptyState('📚', '加载本地文献库…');
    try {
      const data = await api('/api/library?limit=100');
      state.lit.library = data.papers || [];
      state.lit.current = data.papers || [];
      if (!data.available) {
        $('#lit-source-badge').textContent = '文献库未就绪';
        $('#lit-source-badge').className = 'badge badge-warn';
        $('#lit-notice').innerHTML = noticeHtml((data.error || '文献库模块未就绪') + '。可先在「检索结果」中浏览 demo fixtures，或完成 T1 后再入库。', 'warn');
        $('#lit-notice').classList.remove('hidden');
        box.innerHTML = emptyState('🗃️', '文献库模块（sciforge.library）尚未就绪，暂无入库文献。');
        return;
      }
      $('#lit-source-badge').textContent = '本地文献库 · ' + (data.total || 0) + ' 篇';
      $('#lit-source-badge').className = 'badge badge-ok';
      $('#lit-notice').classList.add('hidden');
      renderPaperList(box, data.papers || [], 'library', '文献库为空。去「检索结果」把感兴趣的论文加入库吧。');
    } catch (err) {
      box.innerHTML = emptyState('⚠️', '加载失败：' + err.message);
    }
  }

  function paperChatPrompt(paper) {
    return '精读这篇论文：' + (paper.title || paper.id) +
      (paper.id ? '（arXiv: ' + paper.id + '）' : '') +
      (paper.summary ? '\n\n已知摘要：' + paper.summary : '') +
      '\n\n请输出结构化精读笔记：元信息 / 一句话总结 / 问题设定 / 方法拆解 / 实验证据 / 批判性评估 / 可迁移点。';
  }

  function syncPublisherSelect() {
    const source = $('#lit-source').value;
    const publisher = $('#lit-publisher');
    if (!publisher) return;
    publisher.classList.toggle('hidden', source !== 'crossref');
  }

  function bindLiterature() {
    $('#lit-source').addEventListener('change', syncPublisherSelect);
    $('#lit-form').addEventListener('submit', (event) => {
      event.preventDefault();
      runLiteratureSearch($('#lit-query').value.trim());
    });
    $$('#lit-tabs .tab').forEach((tab) => {
      tab.addEventListener('click', () => {
        if (tab.dataset.tab === 'search') runLiteratureSearch($('#lit-query').value.trim());
        else if (tab.dataset.tab === 'daily') loadDaily();
        else loadLibrary();
      });
    });
    $('#lit-results').addEventListener('click', async (event) => {
      const btn = event.target.closest('button[data-act]');
      if (!btn) return;
      const paper = state.lit.current[Number(btn.dataset.idx)];
      if (!paper) return;
      if (btn.dataset.act === 'read') {
        setPage('chat');
        const input = $('#chat-input');
        input.value = paperChatPrompt(paper);
        autoResizeInput();
        sendChat(input.value);
        input.value = '';
        autoResizeInput();
      } else if (btn.dataset.act === 'add') {
        btn.disabled = true;
        btn.textContent = '入库中…';
        try {
          const data = await apiPost('/api/library/add', paper);
          if (data.ok) {
            toast('已加入文献库：' + paper.title, 'ok');
            btn.textContent = '已入库';
          } else {
            toast(data.error || '入库失败', 'err', 7000);
            btn.disabled = false;
            btn.textContent = '加入库';
          }
        } catch (err) {
          toast('入库失败：' + err.message, 'err', 7000);
          btn.disabled = false;
          btn.textContent = '加入库';
        }
      }
    });
  }

  function loadLiterature() {
    runLiteratureSearch('');
  }

  /* ---------------------------------- 文献检索设置 ---------------------------------- */
  async function loadLitConfig() {
    try {
      const data = await api('/api/literature/config');
      state.lit.configLoaded = true;
      $('#lit-email').value = data.email || '';
      $('#lit-unpaywall-email').value = data.unpaywall_email || '';
      $('#lit-daily-language').value = data.daily_arxiv_language || 'Chinese';

      const badge = $('#lit-config-badge');
      if (data.email || data.unpaywall_email) {
        badge.textContent = 'OA 邮箱已配置';
        badge.className = 'badge badge-ok';
      } else {
        badge.textContent = 'OA 邮箱未配置（可选）';
        badge.className = 'badge badge-ghost';
      }

      const searchSources = data.search_sources || [];
      $('#lit-sources').innerHTML = searchSources.map((source) =>
        '<span class="tag-chip" title="' + escapeHtml(source.note || '') + '">' +
        escapeHtml(source.name) + ' · 检索</span>'
      ).join('');

      const fulltextSources = data.fulltext_sources || [];
      $('#lit-fulltext').innerHTML = fulltextSources.map((source) =>
        '<span class="tag-chip" title="' + escapeHtml(source.note || '') + '">' +
        escapeHtml(source.name) + (source.available ? ' · 可用' : ' · 未配置') + '</span>'
      ).join('');

      const publishers = data.publisher_filters || [];
      $('#lit-publishers').innerHTML = publishers.map((item) =>
        '<span class="tag-chip" title="Crossref member 过滤">' + escapeHtml(item.label || item.id) + '</span>'
      ).join('');

      if (data.policy) {
        $('#lit-config-notice').innerHTML = noticeHtml(data.policy, 'warn');
      }
      if (data.note) {
        $('#lit-config-notice').innerHTML += noticeHtml(data.note + ' 配置路径：' + data.config_path, 'info');
      }
    } catch (err) {
      $('#lit-config-notice').innerHTML = noticeHtml('读取检索设置失败：' + err.message, 'err');
    }
  }

  async function saveLitConfig(event) {
    if (event) event.preventDefault();
    const body = {
      email: $('#lit-email').value.trim(),
      unpaywall_email: $('#lit-unpaywall-email').value.trim(),
      daily_arxiv_language: $('#lit-daily-language').value,
    };
    try {
      const data = await apiPost('/api/literature/config', body);
      toast(data.note || '检索设置已保存', 'ok', 5000);
      await loadLitConfig();
    } catch (err) {
      toast('保存检索设置失败：' + err.message, 'err', 8000);
      $('#lit-config-notice').innerHTML = noticeHtml('保存失败：' + err.message, 'err');
    }
  }

  function bindLitConfig() {
    $('#lit-config-form').addEventListener('submit', saveLitConfig);
    $('#lit-config-refresh').addEventListener('click', loadLitConfig);
  }

  /* ---------------------------------- 服务器 / GPU ---------------------------------- */
  function serverCard(server) {
    const rows = [
      ['host', server.host + ':' + server.port],
      ['user', server.user],
      ['workdir', server.workdir],
      ['conda', server.conda_env || '—'],
      ['scheduler', server.scheduler || 'none'],
      ['GPU', server.gpu_type || '—'],
      ['auth', server.auth],
      ['identity', server.identity_file || '—'],
    ];
    const name = escapeHtml(server.name);
    return '' +
      '<article class="server-card">' +
      '<div class="server-name"><span class="dot dot-ok"></span>' + name +
      '<span class="server-host">' + escapeHtml(server.host) + ':' + escapeHtml(server.port) + '</span></div>' +
      '<dl class="server-rows">' +
      rows.map((pair) => '<dt>' + escapeHtml(pair[0]) + '</dt><dd>' + escapeHtml(pair[1] || '—') + '</dd>').join('') +
      '</dl>' +
      '<div class="pc-actions">' +
      '<button type="button" class="btn btn-ghost btn-sm" data-gpu-server="' + name + '">探测 GPU</button>' +
      '<button type="button" class="btn btn-ghost btn-sm" data-test-server="' + name + '">测试 SSH</button>' +
      '<button type="button" class="btn btn-ghost btn-sm" data-edit-server="' + name + '">编辑</button>' +
      '<button type="button" class="btn btn-ghost btn-sm" data-delete-server="' + name + '">删除</button>' +
      '</div>' +
      '</article>';
  }

  function renderServers(data) {
    const list = $('#srv-list');
    state.servers = data.servers || [];
    $('#srv-count').textContent = (data.count || 0) + ' 台';
    if (!state.servers.length) {
      $('#srv-notice').innerHTML = noticeHtml(data.notice || '尚未配置 GPU 服务器。填写上方表单并保存即可。', 'warn');
      list.innerHTML = emptyState('🖥️', '尚未配置 GPU 服务器。在上方「添加 / 编辑服务器」填写 host、用户、私钥路径后保存。');
      return;
    }
    $('#srv-notice').innerHTML = '';
    list.innerHTML = state.servers.map(serverCard).join('');
    const uploadTarget = $('#srv-upload-target');
    if (uploadTarget) {
      const current = uploadTarget.value;
      uploadTarget.innerHTML = state.servers.map((item) =>
        '<option value="' + escapeHtml(item.name) + '">' + escapeHtml(item.name) + ' — ' + escapeHtml(item.host) + '</option>'
      ).join('') || '<option value="">（请先添加服务器）</option>';
      if (current) uploadTarget.value = current;
    }
    list.querySelectorAll('button[data-gpu-server]').forEach((btn) => {
      btn.addEventListener('click', () => probeGpu(btn.dataset.gpuServer));
    });
    list.querySelectorAll('button[data-test-server]').forEach((btn) => {
      btn.addEventListener('click', () => testServer(btn.dataset.testServer));
    });
    list.querySelectorAll('button[data-edit-server]').forEach((btn) => {
      btn.addEventListener('click', () => fillServerForm(btn.dataset.editServer));
    });
    list.querySelectorAll('button[data-delete-server]').forEach((btn) => {
      btn.addEventListener('click', () => deleteServer(btn.dataset.deleteServer));
    });
  }

  async function loadServersPage() {
    try {
      const data = await api('/api/servers');
      renderServers(data);
      if (!state.gpuProbed) {
        state.gpuProbed = true;
        probeGpu(state.servers.length ? state.servers[0].name : null);
      }
    } catch (err) {
      $('#srv-list').innerHTML = emptyState('⚠️', '读取服务器配置失败：' + err.message);
    }
  }

  function gpuTable(gpus) {
    let memTotal = 0;
    let memUsed = 0;
    let utilSum = 0;
    let utilCount = 0;
    gpus.forEach((gpu) => {
      const total = num(gpu.memory_total);
      const used = num(gpu.memory_used);
      const util = num(gpu.utilization);
      if (total) memTotal += total;
      if (used) memUsed += used;
      if (util !== null) { utilSum += util; utilCount += 1; }
    });
    const avgUtil = utilCount ? Math.round(utilSum / utilCount) : null;
    const summary = '<div class="gpu-summary">' +
      '<div class="gpu-pill"><span>GPU 数量</span><strong>' + gpus.length + '</strong></div>' +
      '<div class="gpu-pill"><span>平均利用率</span><strong>' + (avgUtil === null ? '—' : avgUtil + '%') + '</strong></div>' +
      '<div class="gpu-pill"><span>显存占用</span><strong>' + (memTotal ? (memUsed / 1024).toFixed(1) + ' / ' + (memTotal / 1024).toFixed(1) + ' GiB' : '—') + '</strong></div>' +
      '</div>';

    const body = gpus.map((gpu, idx) => {
      const used = num(gpu.memory_used);
      const total = num(gpu.memory_total);
      const memPct = (used !== null && total) ? Math.min(100, Math.round((used / total) * 100)) : null;
      const util = num(gpu.utilization);
      const memCell = (used !== null || total !== null)
        ? '<span class="util-bar"><i class="' + (memPct !== null && memPct > 85 ? 'hot' : '') + '" style="width:' + (memPct || 0) + '%"></i></span>' +
          escapeHtml((used === null ? '—' : (used / 1024).toFixed(1)) + ' / ' + (total === null ? '—' : (total / 1024).toFixed(1)) + ' GiB')
        : '—';
      const utilCell = util === null ? '—'
        : '<span class="util-bar"><i class="' + (util > 85 ? 'hot' : '') + '" style="width:' + Math.min(100, util) + '%"></i></span>' + util + '%';
      return '<tr>' +
        '<td>' + escapeHtml(gpu.index === null || gpu.index === undefined ? idx : gpu.index) + '</td>' +
        '<td>' + escapeHtml(gpu.name || '—') + '</td>' +
        '<td>' + memCell + '</td>' +
        '<td>' + utilCell + '</td>' +
        '<td>' + escapeHtml(gpu.temperature || '—') + '</td>' +
        '<td>' + escapeHtml(gpu.server || '—') + '</td>' +
        '</tr>';
    }).join('');

    return summary +
      '<div class="table-wrap"><table class="table"><thead><tr><th>#</th><th>型号</th><th>显存</th><th>利用率</th><th>温度</th><th>服务器</th></tr></thead><tbody>' +
      body + '</tbody></table></div>';
  }

  async function probeGpu(serverName) {
    const body = $('#gpu-body');
    $('#gpu-server-label').textContent = serverName ? ('目标：' + serverName) : '目标：默认服务器';
    body.innerHTML = emptyState('🛰️', '正在探测 GPU（SSH nvidia-smi，最多等待 15s）…');
    try {
      const data = await api('/api/gpu' + (serverName ? '?server=' + encodeURIComponent(serverName) : ''));
      if (!data.available) {
        body.innerHTML = noticeHtml(data.error || '远程执行模块未就绪', 'warn') +
          emptyState('🛰️', 'GPU 实时状态不可用。T2 远程执行模块（sciforge.tools.remote）就绪后即可展示。');
        return;
      }
      if (data.error && !(data.gpus || []).length) {
        body.innerHTML = noticeHtml(data.error, 'warn') + (data.content ? '<pre class="code-block">' + escapeHtml(data.content) + '</pre>' : '');
        return;
      }
      if (!(data.gpus || []).length) {
        const local = data.data || {};
        const pills = [];
        if (local.platform) pills.push(['本机平台', local.platform + (local.arch ? ' / ' + local.arch : '')]);
        if (typeof local.mps === 'boolean') pills.push(['Apple MPS', local.mps ? '可用' : '不可用']);
        if (local.server) pills.push(['目标', String(local.server)]);
        const summary = pills.length
          ? '<div class="gpu-summary">' + pills.map((pair) =>
              '<div class="gpu-pill"><span>' + escapeHtml(pair[0]) + '</span><strong>' + escapeHtml(pair[1]) + '</strong></div>'
            ).join('') + '</div>'
          : '';
        body.innerHTML = summary +
          noticeHtml('已连接，但当前环境没有可用的 NVIDIA GPU 行数据。' + (data.content ? '' : '可能是本机无 NVIDIA GPU 或远端 nvidia-smi 不可用。'), 'warn') +
          (data.content ? '<pre class="code-block" style="border:1px solid var(--border-soft);border-radius:var(--radius-xs)">' + escapeHtml(data.content) + '</pre>' : '');
        return;
      }
      body.innerHTML = gpuTable(data.gpus);
    } catch (err) {
      body.innerHTML = noticeHtml('GPU 探测失败：' + err.message, 'err');
    }
  }

  /* ---------------- 服务器配置表单 ---------------- */
  function showTestResult(container, ok, title, detailText) {
    const node = $(container);
    node.classList.remove('hidden');
    node.innerHTML = '<div class="test-result ' + (ok ? 'ok' : 'err') + '">' +
      '<div class="tr-head"><span>' + escapeHtml(title) + '</span>' +
      '<span class="badge ' + (ok ? 'badge-ok' : 'badge-err') + '">' + (ok ? '成功' : '失败') + '</span></div>' +
      (detailText
        ? '<pre class="code-block" style="white-space:pre-wrap;overflow:auto;max-height:260px">' + escapeHtml(detailText) + '</pre>'
        : '') +
      '</div>';
  }

  function serverByName(name) {
    return state.servers.find((item) => item.name === name) || null;
  }

  function fillServerForm(name) {
    const server = serverByName(name);
    if (!server) return;
    $('#srv-name').value = server.name || '';
    $('#srv-host').value = server.host || '';
    $('#srv-port').value = server.port || 22;
    $('#srv-user').value = server.user || 'root';
    $('#srv-key').value = server.identity_file || '';
    $('#srv-proxy').value = server.proxy_jump || '';
    $('#srv-workdir').value = server.workdir || '~/sciforge';
    $('#srv-conda').value = server.conda_env || '';
    $('#srv-tmux').value = server.tmux_session || 'sciforge';
    $('#srv-scheduler').value = server.scheduler || 'none';
    $('#srv-partition').value = server.partition || '';
    $('#srv-parallel').value = server.max_parallel_jobs || 2;
    $('#srv-password').value = '';
    $('#srv-clear-password').checked = false;
    $('#srv-command').value = 'ssh -p ' + (server.port || 22) + ' ' + (server.user || 'root') + '@' + (server.host || '');
    $('#srv-form').scrollIntoView({ behavior: 'smooth', block: 'center' });
    toast('已载入服务器 ' + name + '，修改后点击保存', 'info', 3500);
  }

  function resetServerForm() {
    ['srv-name', 'srv-host', 'srv-key', 'srv-proxy', 'srv-conda', 'srv-partition', 'srv-command', 'srv-password'].forEach((id) => {
      const node = document.getElementById(id);
      if (node) node.value = '';
    });
    $('#srv-port').value = 22;
    $('#srv-user').value = 'root';
    $('#srv-workdir').value = '~/sciforge';
    $('#srv-tmux').value = 'sciforge';
    $('#srv-scheduler').value = 'none';
    $('#srv-parallel').value = 2;
    $('#srv-clear-password').checked = false;
    $('#srv-form-result').classList.add('hidden');
  }

  function collectServerForm() {
    const body = {
      name: $('#srv-name').value.trim(),
      host: $('#srv-host').value.trim(),
      port: Number($('#srv-port').value || 22),
      user: $('#srv-user').value.trim() || 'root',
      identity_file: $('#srv-key').value.trim() || null,
      proxy_jump: $('#srv-proxy').value.trim() || null,
      workdir: $('#srv-workdir').value.trim() || '~/sciforge',
      conda_env: $('#srv-conda').value.trim() || null,
      tmux_session: $('#srv-tmux').value.trim() || 'sciforge',
      scheduler: $('#srv-scheduler').value || 'none',
      partition: $('#srv-partition').value.trim() || null,
      max_parallel_jobs: Number($('#srv-parallel').value || 2),
      auto_mkdir: true,
      clear_password: $('#srv-clear-password').checked,
    };
    const password = $('#srv-password').value;
    if (password) body.password = password;
    return body;
  }

  async function saveServer(andTest) {
    const form = document.getElementById('srv-form');
    if (form && !form.reportValidity()) return;
    try {
      const data = await apiPost('/api/servers', collectServerForm());
      toast(data.note || ('已保存服务器 ' + data.server.name), 'ok', 5000);
      await loadServersPage();
      if (andTest) await testServer(data.server.name);
    } catch (err) {
      showTestResult('#srv-form-result', false, '保存服务器失败', err.message);
    }
  }

  async function testServer(name) {
    showTestResult('#srv-form-result', true, '正在测试 SSH：' + name + '（最多 30s）', '');
    try {
      const data = await apiPost('/api/servers/' + encodeURIComponent(name) + '/test', {});
      if (!data.available) {
        showTestResult('#srv-form-result', false, '远程执行模块不可用', data.error || '');
        return;
      }
      if (!data.ok) {
        showTestResult('#srv-form-result', false, 'SSH 连接失败：' + name,
          (data.error || '') + (data.info ? '\n\n' + prettyJson(data.info) : ''));
        return;
      }
      const info = data.info || {};
      const lines = [
        '延迟: ' + (data.latency_ms || 0) + ' ms',
        'OS: ' + (info.os || '—'),
        'Python: ' + (info.python || '—'),
        'Torch: ' + (info.torch || '—'),
        'GPU: ' + (info.gpu || '—'),
        'workdir: ' + (info.workdir || '—'),
        'workdir 就绪: ' + (info.workdir_ready === undefined ? '—' : (info.workdir_ready ? '是' : '否')),
      ];
      showTestResult('#srv-form-result', true, 'SSH 连接成功：' + name, lines.join('\n'));
    } catch (err) {
      showTestResult('#srv-form-result', false, 'SSH 测试失败', err.message);
    }
  }

  async function deleteServer(name) {
    if (!window.confirm('删除服务器 ' + name + '？只修改本机配置，不影响远端。')) return;
    try {
      await api('/api/servers/' + encodeURIComponent(name), { method: 'DELETE' });
      toast('已删除服务器 ' + name, 'ok');
      await loadServersPage();
    } catch (err) {
      toast('删除失败：' + err.message, 'err', 7000);
    }
  }

  async function parseSshCommand() {
    const command = $('#srv-command').value.trim();
    if (!command) {
      toast('先粘贴 ssh 登录命令，例如：ssh -p 48322 root@host', 'warn', 5000);
      return;
    }
    try {
      const data = await apiPost('/api/servers/parse', { command: command });
      const parsed = data.parsed || {};
      if (parsed.host) $('#srv-host').value = parsed.host;
      if (parsed.port) $('#srv-port').value = parsed.port;
      if (parsed.user) $('#srv-user').value = parsed.user;
      if (parsed.identity_file) $('#srv-key').value = parsed.identity_file;
      if (parsed.proxy_jump) $('#srv-proxy').value = parsed.proxy_jump;
      if (!$('#srv-name').value.trim() && parsed.host) {
        $('#srv-name').value = String(parsed.host).split('.')[0] || 'gpu';
      }
      toast('已解析：' + (parsed.user || '') + '@' + parsed.host + ':' + parsed.port, 'ok', 4000);
    } catch (err) {
      toast('解析失败：' + err.message, 'err', 8000);
    }
  }

  async function uploadFilesToServer() {
    const server = $('#srv-upload-target').value;
    const input = $('#srv-upload-files');
    const files = Array.from(input.files || []);
    const notice = $('#srv-upload-notice');
    notice.classList.remove('hidden');
    if (!server) {
      notice.innerHTML = noticeHtml('请先在下方添加一台 GPU 服务器', 'warn');
      return;
    }
    if (!files.length) {
      notice.innerHTML = noticeHtml('请选择要上传的文件', 'warn');
      return;
    }
    const button = $('#srv-upload-btn');
    const original = button.textContent;
    button.disabled = true;
    button.textContent = '上传中…';
    notice.innerHTML = noticeHtml('正在上传 ' + files.length + ' 个文件到 ' + server + '（浏览器 → 本机 → rsync/scp，最多 180s）…', 'info');
    const form = new FormData();
    files.forEach((file) => form.append('files', file));
    const remoteDir = $('#srv-upload-dir').value.trim();
    if (remoteDir) form.append('remote_dir', remoteDir);
    try {
      const resp = await fetch('/api/servers/' + encodeURIComponent(server) + '/upload', { method: 'POST', body: form });
      const data = await resp.json().catch(() => null);
      if (!resp.ok) throw new Error((data && (data.detail || data.error)) || ('HTTP ' + resp.status));
      if (!data.ok) {
        notice.innerHTML = noticeHtml('上传失败：' + (data.error || '未知错误'), 'err') +
          (data.stderr ? '<pre class="code-block">' + escapeHtml(data.stderr) + '</pre>' : '');
        return;
      }
      const lines = [
        '服务器: ' + data.server,
        '远端目录: ' + data.remote_dir,
        '文件: ' + (data.files || []).map((item) => item.name).join(', '),
        '耗时: ' + data.latency_s + 's',
        '',
        (data.stdout || data.stderr || 'ok').trim(),
      ];
      notice.innerHTML = noticeHtml('上传成功', 'info') + '<pre class="code-block">' + escapeHtml(lines.join('\n')) + '</pre>';
      input.value = '';
      toast('已上传 ' + (data.files || []).length + ' 个文件到 ' + data.server, 'ok', 5000);
    } catch (err) {
      notice.innerHTML = noticeHtml('上传失败：' + err.message, 'err');
      toast('上传失败：' + err.message, 'err', 8000);
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  }

  function bindServers() {
    $('#srv-upload-btn').addEventListener('click', uploadFilesToServer);
    $('#srv-parse-btn').addEventListener('click', parseSshCommand);
    $('#srv-command').addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        parseSshCommand();
      }
    });
    $('#gpu-refresh').addEventListener('click', () => {
      const target = state.servers.length ? state.servers[0].name : null;
      probeGpu(target);
    });
    $('#srv-form').addEventListener('submit', (event) => {
      event.preventDefault();
      saveServer(false);
    });
    $('#srv-save-test').addEventListener('click', () => saveServer(true));
    $('#srv-form-reset').addEventListener('click', resetServerForm);
  }

  /* ---------------------------------- 实验 / Pipeline ---------------------------------- */
  async function loadRecipes() {
    try {
      const data = await api('/api/recipes');
      const select = $('#pipe-recipe');
      const current = select.value;
      select.innerHTML = (data.recipes || []).map((recipe) =>
        '<option value="' + escapeHtml(recipe.name) + '">' + escapeHtml(recipe.name) + ' — ' + escapeHtml(recipe.title || '') + '</option>'
      ).join('');
      if (current) select.value = current;
      if (data.available === false && data.error) {
        $('#pipe-notice').innerHTML = noticeHtml(data.error + '（仍可尝试启动，端点会返回明确错误）', 'warn');
        $('#pipe-notice').classList.remove('hidden');
      }
    } catch (err) {
      toast('读取 recipe 失败：' + err.message, 'err');
    }
  }

  function statusBadgeClass(status) {
    if (status === 'done') return 'badge badge-ok';
    if (status === 'failed') return 'badge badge-err';
    if (status === 'partial') return 'badge badge-warn';
    return 'badge badge-accent';
  }

  function stageMarkerClass(stage) {
    if (stage.skipped) return 'stage-marker';
    if (stage.ok) return 'stage-marker done';
    return 'stage-marker failed';
  }

  function renderPipeline(snapshot) {
    const run = snapshot.run || snapshot;
    const status = run.status || 'running';
    const badge = $('#pipe-status-badge');
    badge.textContent = status;
    badge.className = statusBadgeClass(status);
    $('#pipe-live-panel').classList.remove('hidden');
    $('#pipe-elapsed').textContent = run.duration !== null && run.duration !== undefined ? ('耗时 ' + fmtDuration(run.duration)) : '运行中…';

    const meta = [
      ['API Run ID', run.api_run_id || '—'],
      ['Pipeline Run ID', run.pipeline_run_id || run.run_id || '—'],
      ['Recipe', run.recipe || '—'],
      ['Mock', run.mock ? '是（离线）' : '否'],
      ['开始时间', run.started_at || '—'],
      ['结束时间', run.finished_at || '—'],
      ['主题', run.topic || '—'],
      ['错误', run.error || '—'],
    ];
    $('#pipe-meta').innerHTML = meta.map((pair) =>
      '<div class="kv-item"><span class="kv-key">' + escapeHtml(pair[0]) + '</span><span class="kv-val">' + escapeHtml(pair[1]) + '</span></div>'
    ).join('');

    const stages = run.stages || [];
    let stagesHtml = stages.map((stage) => {
      const marker = stageMarkerClass(stage);
      const icon = stage.skipped ? '⏭' : (stage.ok ? '✓' : '✕');
      const duration = stage.duration !== undefined ? fmtDuration(stage.duration) : '';
      const artifacts = (stage.artifacts || []).map((path) => String(path).split('/').pop()).join(', ');
      return '<div class="stage-row"><div class="' + marker + '">' + icon + '</div>' +
        '<div class="stage-body"><div class="stage-title">' + escapeHtml(stage.title || stage.name || '') +
        (duration ? ' <span class="muted">' + escapeHtml(duration) + '</span>' : '') + '</div>' +
        (stage.error ? '<div class="stage-sub" style="color:var(--err)">' + escapeHtml(stage.error) + '</div>' : '') +
        (artifacts ? '<div class="stage-sub">产物：' + escapeHtml(artifacts) + '</div>' : '') +
        '</div></div>';
    }).join('');

    if (status === 'running') {
      const events = run.events || [];
      let currentName = '';
      for (let i = events.length - 1; i >= 0; i -= 1) {
        const payload = events[i].payload || {};
        if (events[i].kind === 'stage' && payload.phase === 'start' && payload.name) { currentName = payload.title || payload.name; break; }
      }
      stagesHtml += '<div class="stage-row"><div class="stage-marker running">◌</div>' +
        '<div class="stage-body"><div class="stage-title">' + escapeHtml(currentName || '等待第一个阶段…') + '</div>' +
        '<div class="stage-sub">运行中，每秒轮询 /api/pipeline/{run_id}</div></div></div>';
    }
    $('#pipe-stages').innerHTML = stagesHtml || emptyState('🧪', '暂无阶段信息');

    const events = (run.events || []).slice(-120);
    $('#pipe-events').textContent = events.length
      ? events.map((event) => {
          const payload = prettyJson(event.payload || {});
          return '[' + (event.ts ? new Date(event.ts * 1000).toLocaleTimeString('zh-CN') : '') + '] ' + event.kind + ' ' + shortText(payload, 220);
        }).join('\n')
      : '（尚无事件）';

    const links = snapshot.artifact_links || [];
    $('#pipe-artifacts').innerHTML = links.length
      ? '<div class="tool-section-label">产物文件</div><div class="file-links">' + links.map((link) =>
          '<a class="file-link" href="' + encodeURI(link.url) + '" target="_blank" rel="noopener">📎 ' + escapeHtml(String(link.path).split('/').pop()) + '</a>'
        ).join('') + '</div>'
      : '';
  }

  async function pollPipeline() {
    const runId = state.pipe.runId;
    if (!runId) return;
    try {
      const data = await api('/api/pipeline/' + encodeURIComponent(runId));
      const run = data.run || data;
      state.pipe.last = data;
      renderPipeline(data);
      if (run.status && run.status !== 'running') {
        clearInterval(state.pipe.timer);
        state.pipe.timer = null;
        toast('Pipeline 运行结束：' + run.status, run.status === 'done' ? 'ok' : 'warn', 6000);
        loadRuns();
        state.artifactsLoaded = false;
        return;
      }
      if (run.duration && run.duration > 1800) {
        clearInterval(state.pipe.timer);
        state.pipe.timer = null;
        toast('已轮询 30 分钟，停止自动刷新', 'warn');
      }
    } catch (err) {
      clearInterval(state.pipe.timer);
      state.pipe.timer = null;
      toast('轮询 pipeline 状态失败：' + err.message, 'err', 7000);
    }
  }

  async function startPipeline(event) {
    event.preventDefault();
    const topic = $('#pipe-topic').value.trim();
    if (!topic) { toast('请填写研究主题', 'warn'); $('#pipe-topic').focus(); return; }
    const btn = $('#pipe-run-btn');
    btn.disabled = true;
    btn.textContent = '启动中…';
    $('#pipe-notice').classList.add('hidden');
    try {
      const data = await apiPost('/api/pipeline/run', {
        recipe: $('#pipe-recipe').value,
        topic: topic,
        mock: $('#pipe-mock').checked,
      });
      if (!data.ok || data.available === false) {
        $('#pipe-notice').innerHTML = noticeHtml(data.error || 'Pipeline 引擎未就绪', 'err');
        $('#pipe-notice').classList.remove('hidden');
        return;
      }
      toast('已启动 pipeline：' + data.run_id + (data.mock_reason ? '（离线 Mock）' : ''), 'ok', 6000);
      state.pipe.runId = data.run_id;
      state.pipe.last = null;
      $('#pipe-live-panel').classList.remove('hidden');
      $('#pipe-status-badge').textContent = 'running';
      $('#pipe-status-badge').className = 'badge badge-accent';
      $('#pipe-elapsed').textContent = '运行中…';
      $('#pipe-meta').innerHTML = '';
      $('#pipe-stages').innerHTML = emptyState('⏳', '已提交，等待第一个阶段…');
      $('#pipe-events').textContent = '（等待事件）';
      $('#pipe-artifacts').innerHTML = '';
      if (state.pipe.timer) clearInterval(state.pipe.timer);
      state.pipe.timer = setInterval(pollPipeline, 1200);
      pollPipeline();
    } catch (err) {
      $('#pipe-notice').innerHTML = noticeHtml('启动失败：' + err.message, 'err');
      $('#pipe-notice').classList.remove('hidden');
    } finally {
      btn.disabled = false;
      btn.textContent = '启动运行';
    }
  }

  function renderRuns(data) {
    const body = $('#runs-body');
    const rows = (data.runs || []).slice();
    const active = (data.active || []).filter((run) => run.live);
    if (!rows.length && !active.length) {
      body.innerHTML = emptyState('🧪', '还没有 pipeline 运行记录。填写主题后点击「启动运行」，mock 模式无需 API key。');
      return;
    }
    let html = '<div class="table-wrap"><table class="table runs-table"><thead><tr><th>Run</th><th>Recipe</th><th>状态</th><th>主题</th><th>产物</th></tr></thead><tbody>';
    active.forEach((run) => {
      html += '<tr><td><code>' + escapeHtml(shortText(run.api_run_id || run.run_id, 26)) + '</code></td>' +
        '<td>' + escapeHtml(run.recipe || '—') + '</td>' +
        '<td><span class="run-status running">running</span></td>' +
        '<td>' + escapeHtml(shortText(run.topic || '', 50)) + '</td>' +
        '<td><span class="muted">轮询中…</span></td></tr>';
    });
    rows.forEach((run) => {
      const status = run.status || 'unknown';
      const statusClass = ['done', 'failed', 'partial', 'running'].includes(status) ? status : 'running';
      const report = run.report_url
        ? '<a class="file-link" href="' + encodeURI(run.report_url) + '" target="_blank" rel="noopener">REPORT.md</a>'
        : '<span class="muted">—</span>';
      const started = run.started_at ? new Date(run.started_at * 1000 || run.started_at).toLocaleString('zh-CN') : '—';
      html += '<tr><td><code>' + escapeHtml(shortText(run.run_id || '—', 30)) + '</code><div class="muted">' + escapeHtml(started) + '</div></td>' +
        '<td>' + escapeHtml(run.recipe || '—') + '</td>' +
        '<td><span class="run-status ' + statusClass + '">' + escapeHtml(status) + '</span></td>' +
        '<td>' + escapeHtml(shortText(run.topic || '', 50)) + '</td>' +
        '<td>' + report + '</td></tr>';
    });
    html += '</tbody></table></div>';
    body.innerHTML = html;
  }

  async function loadRuns() {
    try {
      const data = await api('/api/runs');
      renderRuns(data);
    } catch (err) {
      $('#runs-body').innerHTML = emptyState('⚠️', '读取运行历史失败：' + err.message);
    }
  }

  async function loadExperimentsPage() {
    await loadRecipes();
    await loadRuns();
    await loadMetricsServers();
    fetchMetrics();
  }

  /* ---------------------------------- GPU 实时指标 ---------------------------------- */
  async function loadMetricsServers() {
    const select = $('#metrics-server');
    if (!select) return;
    try {
      const data = await api('/api/servers');
      const current = select.value;
      select.innerHTML = (data.servers || []).map((item) =>
        '<option value="' + escapeHtml(item.name) + '">' + escapeHtml(item.name) + ' — ' + escapeHtml(item.host) + '</option>'
      ).join('') || '<option value="">（请先在服务器页添加 GPU）</option>';
      if (current) select.value = current;
    } catch (err) { /* 服务器读取失败时保留空选项 */ }
  }

  function stopMetricsAuto() {
    if (state.metrics.timer) {
      clearInterval(state.metrics.timer);
      state.metrics.timer = null;
    }
    const toggle = $('#metrics-auto');
    if (toggle) toggle.checked = false;
  }

  async function fetchMetrics() {
    const server = $('#metrics-server').value;
    const notice = $('#metrics-notice');
    const summary = $('#metrics-summary');
    notice.classList.remove('hidden');
    if (!server) {
      notice.innerHTML = noticeHtml('请先在「服务器」页添加并测试 GPU 服务器', 'warn');
      summary.innerHTML = '';
      $('#metrics-chart-wrap').classList.add('hidden');
      return;
    }
    notice.innerHTML = noticeHtml('正在读取 ' + server + ' 上的实验指标…', 'info');
    try {
      const query = $('#metrics-path').value.trim();
      const data = await api('/api/servers/' + encodeURIComponent(server) + '/metrics' + (query ? '?path=' + encodeURIComponent(query) : ''));
      if (!data.available) {
        notice.innerHTML = noticeHtml(data.error || '远程模块未就绪', 'warn');
        return;
      }
      if (!data.ok) {
        notice.innerHTML = noticeHtml('读取指标失败：' + (data.error || '未知错误'), 'warn');
        summary.innerHTML = '';
        $('#metrics-chart-wrap').classList.add('hidden');
        return;
      }
      notice.innerHTML = noticeHtml(
        '已读取 ' + (data.path || '') + ' · ' + (data.points || 0) + ' 个记录点 · 延迟 ' + (data.latency_s || 0) + 's' +
        (data.warning ? ' · ' + data.warning : ''), 'info');
      state.metrics.data = data;
      renderMetricsSummary(data);
      renderMetricsChart(data);
      $('#metrics-raw').textContent = data.raw_preview || JSON.stringify(data.latest || {}, null, 2);
    } catch (err) {
      notice.innerHTML = noticeHtml('读取指标失败：' + err.message, 'err');
    }
  }

  function renderMetricsSummary(data) {
    const summary = $('#metrics-summary');
    const latest = data.latest || {};
    const keys = Object.keys(latest).slice(0, 8);
    summary.innerHTML = keys.length
      ? keys.map((key) => '<div class="gpu-pill"><span>' + escapeHtml(key) + '</span><strong>' + escapeHtml(String(latest[key])) + '</strong></div>').join('')
      : '';
  }

  function renderMetricsChart(data) {
    const series = data.series || {};
    const wrap = $('#metrics-chart-wrap');
    const canvas = $('#metrics-chart');
    const names = Object.keys(series).filter((key) => Array.isArray(series[key]) && series[key].length > 1).slice(0, 3);
    if (!names.length) {
      wrap.classList.add('hidden');
      return;
    }
    wrap.classList.remove('hidden');
    const dpr = window.devicePixelRatio || 1;
    const cssWidth = Math.max(320, wrap.clientWidth || canvas.parentElement.clientWidth || 720);
    const cssHeight = 220;
    canvas.width = Math.round(cssWidth * dpr);
    canvas.height = Math.round(cssHeight * dpr);
    canvas.style.width = '100%';
    canvas.style.height = cssHeight + 'px';
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssWidth, cssHeight);
    const pad = { left: 46, right: 12, top: 14, bottom: 26 };
    const width = cssWidth - pad.left - pad.right;
    const height = cssHeight - pad.top - pad.bottom;
    let min = Infinity;
    let max = -Infinity;
    names.forEach((key) => series[key].forEach((value) => {
      if (typeof value === 'number') { min = Math.min(min, value); max = Math.max(max, value); }
    }));
    if (!Number.isFinite(min) || !Number.isFinite(max)) return;
    if (min === max) { max = min + 1; }
    const xOf = (index, total) => pad.left + (total <= 1 ? 0 : (index / (total - 1)) * width);
    const yOf = (value) => pad.top + height - ((value - min) / (max - min)) * height;
    const colors = ['#22d3ee', '#f59e0b', '#a3e635'];
    // 网格
    ctx.strokeStyle = 'rgba(148,163,184,0.18)';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i += 1) {
      const y = pad.top + (i / 4) * height;
      ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(pad.left + width, y); ctx.stroke();
      ctx.fillStyle = 'rgba(148,163,184,0.8)';
      ctx.font = '10px ui-monospace, monospace';
      const value = max - ((max - min) * i) / 4;
      ctx.fillText(value.toFixed(Math.abs(value) < 10 ? 2 : 1), 4, y + 3);
    }
    names.forEach((key, seriesIndex) => {
      const values = series[key].filter((value) => typeof value === 'number');
      ctx.strokeStyle = colors[seriesIndex % colors.length];
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      values.forEach((value, index) => {
        const x = xOf(index, values.length);
        const y = yOf(value);
        if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.fillStyle = colors[seriesIndex % colors.length];
      ctx.font = '10.5px ui-monospace, monospace';
      ctx.fillText(key, pad.left + 8 + seriesIndex * 110, pad.top + 11);
    });
    ctx.fillStyle = 'rgba(148,163,184,0.7)';
    ctx.font = '10px ui-monospace, monospace';
    ctx.fillText('step →', pad.left + width - 42, cssHeight - 8);
  }

  function bindMetrics() {
    const auto = $('#metrics-auto');
    if (auto) {
      auto.addEventListener('change', () => {
        stopMetricsAuto();
        if (auto.checked) {
          state.metrics.timer = setInterval(fetchMetrics, 5000);
          fetchMetrics();
        }
      });
    }
    const refresh = $('#metrics-refresh');
    if (refresh) refresh.addEventListener('click', fetchMetrics);
    const server = $('#metrics-server');
    if (server) server.addEventListener('change', fetchMetrics);
  }

  function bindExperiments() {
    $('#pipe-form').addEventListener('submit', startPipeline);
    $('#runs-refresh').addEventListener('click', loadRuns);
  }

  /* ---------------------------------- 图表 / 产物 ---------------------------------- */
  function thumbHtml(item) {
    if (item.ext === 'pdf') return '<div class="shot-thumb"><span class="placeholder">PDF</span></div>';
    return '<div class="shot-thumb"><img loading="lazy" src="' + encodeURI(item.url) + '" alt="' + escapeHtml(item.name) + '"></div>';
  }

  function bindThumbFallbacks(container) {
    $$('img', container).forEach((img) => {
      img.addEventListener('error', () => {
        const placeholder = el('span', 'placeholder', '⚠');
        img.replaceWith(placeholder);
      }, { once: true });
    });
  }

  function renderArtifacts(data) {
    const artifacts = data.artifacts || [];
    const fixtures = data.fixtures || [];
    const previewable = artifacts.filter((item) => item.previewable);
    const fixturePreviewable = fixtures.filter((item) => item.previewable);
    const gallery = $('#fig-gallery');
    $('#fig-count').textContent = artifacts.length + ' 项产物';
    const shots = previewable.concat(fixturePreviewable);
    if (!shots.length) {
      gallery.innerHTML = emptyState('🖼️', '还没有可预览的图片产物。运行 `sciforge fig arch` 或 pipeline 配图阶段后，这里会自动出现 SVG / PNG / PDF 预览。');
    } else {
      gallery.innerHTML = shots.map((item) =>
        '<a class="shot" href="' + encodeURI(item.url) + '" target="_blank" rel="noopener" title="' + escapeHtml(item.path) + '">' +
        thumbHtml(item) +
        '<div class="shot-body"><span class="shot-name">' + escapeHtml(item.name) + '</span>' +
        '<span class="shot-meta"><span>' + escapeHtml(item.kind) + '</span><span>' + fmtBytes(item.size) + '</span></span></div></a>'
      ).join('');
      bindThumbFallbacks(gallery);
    }

    const files = artifacts.filter((item) => !item.previewable).concat(fixtures.filter((item) => !item.previewable));
    if (!files.length) {
      $('#fig-files').innerHTML = emptyState('🗂️', '暂无数据文件。demo fixtures 中的 arch_spec.yaml / plot_spec.yaml / results/metrics.json 会在这里出现。');
    } else {
      $('#fig-files').innerHTML = files.map((item, index) =>
        '<div class="file-row" data-file-idx="' + index + '">' +
        '<span class="file-icon">' + fileIcon(item.ext) + '</span>' +
        '<span class="file-main"><span class="file-name">' + escapeHtml(item.name) + '</span>' +
        '<span class="file-sub">' + escapeHtml(item.path) + ' · ' + fmtBytes(item.size) + '</span></span>' +
        '<span class="file-kind">' + escapeHtml(item.ext) + '</span></div>'
      ).join('');
      $$('#fig-files .file-row').forEach((row) => {
        row.addEventListener('click', () => {
          const item = files[Number(row.dataset.fileIdx)];
          if (item) openPreview(item);
        });
      });
    }

    const noFiguresNotice = artifacts.length ? '' : noticeHtml(
      '项目里还没有 figures/ 或 runs/ 产物。当前展示 examples/demo/fixtures 中的离线演示数据；运行 pipeline 后会生成真实产物。', 'warn'
    );
    $('#fig-notice').innerHTML = noFiguresNotice;
    $('#fig-notice').classList.toggle('hidden', !noFiguresNotice);
  }

  function fileIcon(ext) {
    if (['json', 'csv'].includes(ext)) return '📊';
    if (['yaml', 'yml'].includes(ext)) return '🧾';
    if (ext === 'md') return '📝';
    if (ext === 'pdf') return '📕';
    if (['png', 'jpg', 'jpeg', 'svg', 'webp', 'gif'].includes(ext)) return '🖼️';
    return '📄';
  }

  async function loadArtifacts() {
    try {
      const data = await api('/api/artifacts');
      state.artifactsLoaded = true;
      renderArtifacts(data);
    } catch (err) {
      $('#fig-gallery').innerHTML = emptyState('⚠️', '读取产物失败：' + err.message);
    }
  }

  function openModal(title, bodyNode) {
    $('#modal-title').textContent = title || '预览';
    const body = $('#modal-body');
    body.innerHTML = '';
    if (bodyNode) body.appendChild(bodyNode);
    $('#modal').classList.remove('hidden');
  }

  function closeModal() {
    $('#modal').classList.add('hidden');
    $('#modal-body').innerHTML = '';
  }

  async function openPreview(item) {
    try {
      if (['png', 'jpg', 'jpeg', 'svg', 'webp', 'gif'].includes(item.ext)) {
        const img = el('img');
        img.src = item.url;
        img.alt = item.name;
        openModal(item.path, img);
      } else if (item.ext === 'pdf') {
        const frame = el('iframe');
        frame.src = item.url;
        frame.title = item.name;
        openModal(item.path, frame);
      } else {
        const text = await fetchText(item.url);
        let content = text;
        if (item.ext === 'json') {
          try { content = JSON.stringify(JSON.parse(text), null, 2); } catch (err) { /* 保留原文 */ }
        }
        if (item.ext === 'md') {
          const div = el('div', 'md');
          div.innerHTML = renderMarkdown(text);
          openModal(item.path, div);
        } else {
          const pre = el('pre', 'code-block');
          pre.style.maxHeight = '68vh';
          pre.textContent = content;
          openModal(item.path, pre);
        }
      }
    } catch (err) {
      toast('打开预览失败：' + err.message, 'err');
    }
  }

  function bindFigures() {
    $('#fig-refresh').addEventListener('click', loadArtifacts);
    $('#modal-close').addEventListener('click', closeModal);
    $('#modal').addEventListener('click', (event) => {
      if (event.target === $('#modal')) closeModal();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closeModal();
    });
  }

  /* ---------------------------------- 大模型配置 ---------------------------------- */
  function providerCard(provider) {
    const keyBadge = provider.key_status === 'config'
      ? '<span class="badge badge-ok">Key 已保存</span>'
      : provider.key_status === 'env'
        ? '<span class="badge badge-ok">Key 来自环境变量</span>'
        : '<span class="badge badge-warn">缺少 Key</span>';
    const models = (provider.models || []).slice(0, 4).map((m) => '<span class="tag-chip">' + escapeHtml(m) + '</span>').join('');
    const name = escapeHtml(provider.name);
    const keyEnv = provider.api_key_env ? ' <code>' + escapeHtml(provider.api_key_env) + '</code>' : '';
    return '<article class="provider-card' + (provider.is_default ? ' is-default' : '') + '">' +
      '<div class="pc-head"><span class="pc-name">' + name + '</span>' +
      (provider.is_default ? '<span class="badge badge-accent">默认</span>' : '') + '</div>' +
      '<div class="pc-row"><span>Base URL</span><code title="' + escapeHtml(provider.base_url) + '">' + escapeHtml(provider.base_url) + '</code></div>' +
      '<div class="pc-row"><span>默认模型</span><code>' + escapeHtml(provider.default_model) + '</code></div>' +
      (models ? '<div class="tag-list">' + models + '</div>' : '') +
      '<div class="pc-row"><span>Key</span><span>' + keyBadge + keyEnv + '</span></div>' +
      '<div class="pc-actions">' +
      '<button class="btn btn-ghost btn-sm" type="button" data-edit-provider="' + name + '">编辑</button>' +
      '<button class="btn btn-ghost btn-sm" type="button" data-test-provider="' + name + '">测试连接</button>' +
      (provider.is_default ? '' : '<button class="btn btn-ghost btn-sm" type="button" data-default-provider="' + name + '">设为默认</button>') +
      '<button class="btn btn-ghost btn-sm" type="button" data-delete-provider="' + name + '">删除</button>' +
      '</div></article>';
  }

  function renderProviders(data) {
    const grid = $('#llm-providers');
    state.llm.providers = data.providers || [];
    state.llm.default = data.default || '';
    $('#llm-provider-count').textContent =
      state.llm.providers.length + ' 个 · 默认 ' + (data.default || '—');
    if (!state.llm.providers.length) {
      grid.innerHTML = emptyState('🧠', '还没有模型 Provider。在下方表单填写 Base URL / 模型 / Key 后保存。');
      return;
    }
    grid.innerHTML = state.llm.providers.map(providerCard).join('');
    grid.querySelectorAll('[data-edit-provider]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const provider = state.llm.providers.find((item) => item.name === btn.dataset.editProvider);
        if (provider) fillProviderForm(provider);
      });
    });
    grid.querySelectorAll('[data-test-provider]').forEach((btn) => {
      btn.addEventListener('click', () => testLlm(btn.dataset.testProvider, null));
    });
    grid.querySelectorAll('[data-default-provider]').forEach((btn) => {
      btn.addEventListener('click', () => makeDefaultProvider(btn.dataset.defaultProvider));
    });
    grid.querySelectorAll('[data-delete-provider]').forEach((btn) => {
      btn.addEventListener('click', () => deleteProvider(btn.dataset.deleteProvider));
    });
  }

  function fillProviderForm(provider) {
    $('#llm-name').value = provider.name || '';
    $('#llm-name').readOnly = true;
    $('#llm-name').style.opacity = '0.65';
    $('#llm-base-url').value = provider.base_url || '';
    $('#llm-default-model').value = provider.default_model || '';
    $('#llm-models').value = (provider.models || []).join(', ');
    $('#llm-api-key').value = '';
    $('#llm-api-key-env').value = provider.api_key_env || '';
    $('#llm-temperature').value = provider.temperature === null || provider.temperature === undefined ? '' : provider.temperature;
    $('#llm-max-tokens').value = provider.max_tokens === null || provider.max_tokens === undefined ? '' : provider.max_tokens;
    $('#llm-clear-key').checked = false;
    $('#llm-make-default').checked = false;
    $('#llm-test-result').classList.add('hidden');
    $('#llm-form').scrollIntoView({ behavior: 'smooth', block: 'center' });
    toast('已载入 Provider ' + provider.name + '（名称不可改），修改后保存', 'info', 4000);
  }

  function resetProviderForm() {
    ['llm-name', 'llm-base-url', 'llm-default-model', 'llm-models', 'llm-api-key', 'llm-api-key-env', 'llm-temperature', 'llm-max-tokens'].forEach((id) => {
      const node = document.getElementById(id);
      if (node) node.value = '';
    });
    $('#llm-name').readOnly = false;
    $('#llm-name').style.opacity = '1';
    $('#llm-clear-key').checked = false;
    $('#llm-make-default').checked = false;
    $('#llm-test-result').classList.add('hidden');
  }

  function collectProviderForm() {
    const body = {
      name: $('#llm-name').value.trim(),
      base_url: $('#llm-base-url').value.trim(),
      default_model: $('#llm-default-model').value.trim(),
      models: $('#llm-models').value.trim(),
      api_key_env: $('#llm-api-key-env').value.trim(),
      clear_api_key: $('#llm-clear-key').checked,
      make_default: $('#llm-make-default').checked,
      supports_tools: true,
    };
    const key = $('#llm-api-key').value.trim();
    if (key) body.api_key = key;
    const temperature = $('#llm-temperature').value.trim();
    if (temperature !== '') body.temperature = Number(temperature);
    const maxTokens = $('#llm-max-tokens').value.trim();
    if (maxTokens !== '') body.max_tokens = Number(maxTokens);
    return body;
  }

  async function saveProvider(andTest) {
    const form = document.getElementById('llm-form');
    if (form && !form.reportValidity()) return;
    try {
      const data = await apiPost('/api/llm/providers', collectProviderForm());
      toast(data.note || ('已保存 Provider ' + data.provider.name), 'ok', 5000);
      $('#llm-api-key').value = '';
      $('#llm-clear-key').checked = false;
      resetProviderForm();
      await loadLlmPage();
      await loadHealth();
      if (andTest) await testLlm(data.provider.name, null);
    } catch (err) {
      showTestResult('#llm-test-result', false, '保存 Provider 失败', err.message);
    }
  }

  async function testLlm(providerName, role) {
    const label = providerName ? ('Provider：' + providerName) : ('角色：' + (role || 'executor'));
    showTestResult('#llm-test-result', true, '正在测试 ' + label + '（最多 45s）', '');
    try {
      const data = await apiPost('/api/llm/test', providerName ? { provider: providerName } : { role: role || 'executor' });
      if (!data.available) {
        showTestResult('#llm-test-result', false, 'LLM 测试不可用', data.error || '');
        return;
      }
      if (!data.ok) {
        showTestResult('#llm-test-result', false, 'LLM 测试失败：' + (data.provider || ''), data.error || '');
        return;
      }
      const usage = data.usage || {};
      const lines = [
        'Provider: ' + (data.provider || '—'),
        'Model: ' + (data.model || '—'),
        '延迟: ' + (data.latency_ms || 0) + ' ms',
        'tokens: ' + (usage.total_tokens || '—'),
        '',
        '回复：',
        (data.content || '').slice(0, 1200),
      ];
      showTestResult('#llm-test-result', true, 'LLM 连接成功：' + (data.provider || ''), lines.join('\n'));
    } catch (err) {
      showTestResult('#llm-test-result', false, 'LLM 测试失败', err.message);
    }
  }

  async function deleteProvider(name) {
    if (!window.confirm('删除 Provider ' + name + '？只修改本机配置，不影响远端服务。')) return;
    try {
      await api('/api/llm/providers/' + encodeURIComponent(name), { method: 'DELETE' });
      toast('已删除 Provider ' + name, 'ok');
      await loadLlmPage();
      await loadHealth();
    } catch (err) {
      toast('删除失败：' + err.message, 'err', 8000);
    }
  }

  async function makeDefaultProvider(name) {
    const provider = state.llm.providers.find((item) => item.name === name);
    if (!provider) return;
    try {
      await apiPost('/api/llm/providers', {
        name: provider.name,
        base_url: provider.base_url,
        default_model: provider.default_model,
        models: (provider.models || []).join(','),
        api_key_env: provider.api_key_env || '',
        make_default: true,
      });
      toast('已设为默认 Provider：' + name, 'ok');
      await loadLlmPage();
      await loadHealth();
    } catch (err) {
      toast('设置默认失败：' + err.message, 'err', 8000);
    }
  }

  function renderRoles(data) {
    const box = $('#llm-roles');
    const roles = data.roles || {};
    state.llm.roles = roles;
    const providers = state.llm.providers;
    box.innerHTML = Object.keys(roles).map((role) => {
      const entry = roles[role] || {};
      const options = providers.map((provider) => {
        const selected = provider.name === entry.provider ? ' selected' : '';
        return '<option value="' + escapeHtml(provider.name) + '"' + selected + '>' + escapeHtml(provider.name) + '</option>';
      }).join('');
      return '<div class="role-row" data-role="' + escapeHtml(role) + '">' +
        '<span class="role-name">' + escapeHtml(role) + '</span>' +
        '<select data-role-provider>' + options + '</select>' +
        '<input data-role-model value="' + escapeHtml(entry.model || '') + '" placeholder="模型（留空用 Provider 默认）">' +
        '</div>';
    }).join('');
  }

  async function saveRoles() {
    const roles = {};
    $$('#llm-roles .role-row').forEach((row) => {
      const provider = row.querySelector('[data-role-provider]');
      const model = row.querySelector('[data-role-model]');
      roles[row.dataset.role] = { provider: provider ? provider.value : null, model: model ? (model.value.trim() || null) : null };
    });
    try {
      const data = await apiPost('/api/llm/roles', { roles: roles });
      $('#llm-roles-notice').textContent = '已保存 ' + Object.keys(roles).length + ' 个角色路由';
      toast('角色路由已保存', 'ok');
      state.llm.roles = data.roles || roles;
      await loadHealth();
    } catch (err) {
      $('#llm-roles-notice').textContent = '保存失败：' + err.message;
      toast('角色路由保存失败：' + err.message, 'err', 8000);
    }
  }

  async function loadLlmPage() {
    try {
      const data = await api('/api/llm');
      state.llm.loaded = true;
      renderProviders(data);
      renderRoles(data);
      $('#llm-notice').innerHTML = noticeHtml(
        '配置写入：' + (data.config_path || '~/.sciforge/config.toml') +
        '（0600）。API Key 不会回传浏览器；无 Key 时自动离线 Mock。', 'info');
      $('#llm-notice').classList.remove('hidden');
    } catch (err) {
      $('#llm-providers').innerHTML = emptyState('⚠️', '读取模型配置失败：' + err.message);
    }
  }

  function bindLlm() {
    $('#llm-refresh').addEventListener('click', loadLlmPage);
    $('#llm-form').addEventListener('submit', (event) => {
      event.preventDefault();
      saveProvider(false);
    });
    $('#llm-save-test').addEventListener('click', () => saveProvider(true));
    $('#llm-form-reset').addEventListener('click', resetProviderForm);
    $('#llm-roles-save').addEventListener('click', saveRoles);
  }

  /* ---------------------------------- 技能 ---------------------------------- */
  function skillCard(skill) {
    const tags = (skill.tags || []).slice(0, 5).map((tag) => '<span class="tag-chip">' + escapeHtml(tag) + '</span>').join('');
    return '<article class="skill-card">' +
      '<div class="skill-head"><span class="skill-name">' + escapeHtml(skill.name) + '</span>' +
      '<span class="badge badge-ghost">' + escapeHtml(skill.source || 'bundled') + '</span></div>' +
      '<div class="skill-desc">' + escapeHtml(skill.description || '（无 description）') + '</div>' +
      (tags ? '<div class="tag-list">' + tags + '</div>' : '') +
      '<div class="skill-foot"><span class="muted">' + escapeHtml(skill.version ? 'v' + skill.version : '') + '</span>' +
      '<button type="button" class="btn btn-ghost btn-sm" data-install="' + escapeHtml(skill.name) + '">安装到 DSH</button></div>' +
      '</article>';
  }

  async function loadSkills(query) {
    const grid = $('#skills-grid');
    grid.innerHTML = emptyState('🧩', '加载技能库…');
    try {
      const data = await api('/api/skills' + (query ? '?query=' + encodeURIComponent(query) : ''));
      state.skillsLoaded = true;
      const badge = $('#skills-source');
      badge.textContent = data.available
        ? 'SkillManager · ' + (data.count || 0) + ' 个'
        : 'skills_lib 只读扫描 · ' + (data.count || 0) + ' 个';
      badge.className = 'badge ' + (data.available ? 'badge-ok' : 'badge-warn');
      if (data.degraded && data.error) {
        $('#skills-notice').innerHTML = noticeHtml(data.error, 'warn');
        $('#skills-notice').classList.remove('hidden');
      } else {
        $('#skills-notice').classList.add('hidden');
      }
      const skills = data.skills || [];
      if (!skills.length) {
        grid.innerHTML = emptyState('🧩', '技能库为空。T4 完成后 sciforge/skills_lib 下的科研 Skill 会出现在这里。');
      } else {
        grid.innerHTML = skills.map(skillCard).join('');
      }
    } catch (err) {
      grid.innerHTML = emptyState('⚠️', '读取技能失败：' + err.message);
    }
  }

  function bindSkills() {
    $('#skills-form').addEventListener('submit', (event) => {
      event.preventDefault();
      loadSkills($('#skills-query').value.trim());
    });
    $('#skills-grid').addEventListener('click', async (event) => {
      const btn = event.target.closest('button[data-install]');
      if (!btn) return;
      const name = btn.dataset.install;
      const original = btn.textContent;
      btn.disabled = true;
      btn.textContent = '安装中…';
      try {
        const data = await apiPost('/api/skills/install-dsh', { name: name });
        if (data.ok) {
          btn.textContent = '已安装';
          toast(data.message || ('已安装到 DSH：' + name), 'ok', 6000);
        } else {
          btn.textContent = original;
          btn.disabled = false;
          toast(data.error || '安装失败', 'err', 8000);
        }
      } catch (err) {
        btn.textContent = original;
        btn.disabled = false;
        toast('安装失败：' + err.message, 'err', 8000);
      }
    });
  }

  /* ---------------------------------- 初始化 ---------------------------------- */
  function bindNav() {
    $$('.nav-item').forEach((item) => {
      item.addEventListener('click', () => setPage(item.dataset.page));
    });
  }

  function bindTheme() {
    $('#theme-toggle').addEventListener('click', () => {
      const current = document.documentElement.getAttribute('data-theme');
      applyTheme(current === 'dark' ? 'light' : 'dark');
    });
  }

  function init() {
    let saved = 'dark';
    try { saved = localStorage.getItem('sciforge-theme') || 'dark'; } catch (err) { /* ignore */ }
    applyTheme(saved);
    bindNav();
    bindTheme();
    bindChat();
    bindLiterature();
    bindLitConfig();
    bindServers();
    bindLlm();
    bindExperiments();
    bindMetrics();
    bindFigures();
    bindSkills();
    loadHealth();
    autoResizeInput();
    window.addEventListener('hashchange', () => {
      const page = pageFromHash();
      if (page && page !== state.page) setPage(page);
    });
    const initialPage = pageFromHash();
    if (initialPage) setPage(initialPage);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
