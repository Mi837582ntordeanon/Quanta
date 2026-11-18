/* Q.W.A.N.T.A. — local UI */
'use strict';

/* ------------------------------------------------------------------ helpers */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const TOOL_LABELS = {
  list_dir: 'Просмотр папки',
  read_file: 'Чтение файла',
  write_file: 'Запись файла',
  search_files: 'Поиск файлов',
  move_path: 'Перемещение',
  copy_path: 'Копирование',
  delete_path: 'Удаление в Корзину',
  run_command: 'Команда',
  open_path: 'Открытие',
  screenshot: 'Скриншот',
  mouse_click: 'Клик мышью',
  type_text: 'Ввод текста',
  press_keys: 'Горячие клавиши',
  volume: 'Громкость',
  clipboard_get: 'Чтение буфера',
  clipboard_set: 'Запись в буфер',
  web_search: 'Поиск в интернете',
  fetch_url: 'Загрузка страницы',
  notes_create: 'Создание заметки',
  notes_list: 'Заметки',
  notes_update: 'Изменение заметки',
  notes_delete: 'Удаление заметки',
  reminder_create: 'Создание напоминания',
  reminder_list: 'Напоминания',
  reminder_delete: 'Удаление напоминания',
  create_3d_model: '3D-модель',
  generate_image: 'Генерация картинки',
  system_info: 'Информация о системе',
  list_processes: 'Процессы',
  kill_process: 'Завершение процесса',
  minecraft_status: 'Статус Minecraft',
  weather: 'Погода',
};

const MODE_LABELS = { safe: 'Безопасный', smart: 'Умный', auto: 'Авто' };
const params = new URLSearchParams(location.search);

const state = {
  settings: null,
  chats: [],
  notes: [],
  reminders: [],
  memories: [],
  projects: [],
  workflows: [],
  actions: [],
  models: [],
  chatId: null,
  chatTitle: 'Новый чат',
  messages: [],
  current: null,
  toolCards: new Map(),
  busy: false,
  abort: null,
  mode: 'standard',
  drawerMode: '',
  editingProjectId: '',
  dataDir: '',
  version: '0.1.0',
  system: null,
  bootTimer: null,
  bootDone: false,
  orbState: 'idle',
  activeTask: '',
  seriousMode: false,
  recording: false,
  voiceStatus: null,
  lastVoiceMetrics: null,
  voiceCaptureEffects: null,
};

const dom = {};
let audioCtx = null;
let modalClosers = [];
let mediaRecorder = null;
let mediaStream = null;
let recordingChunks = [];


function cacheDom() {
  const ids = [
    'boot', 'bootLog', 'bootBar', 'bootSkip', 'app', 'sidebar', 'chatList', 'chatTitle',
    'modeChip', 'resourceProfileSelect', 'modelSelect', 'accessSelect', 'settingsBtn', 'expandBtn', 'newChatBtn',
    'sidebarToggle', 'chatScroll', 'welcome', 'messages', 'input', 'sendBtn', 'stopBtn',
    'micBtn', 'settingsBtn', 'drawer', 'drawerTitle', 'drawerBody', 'drawerClose',
    'modalRoot', 'toasts', 'statusDot', 'statusText', 'clockTime', 'clockDate',
    'seriousModeBtn', 'presenceState', 'presenceDetail', 'presenceActivity',
    'weatherCity', 'weatherIcon', 'weatherTemp', 'weatherDesc', 'weatherRange',
    'cpuBar', 'cpuVal', 'ramBar', 'ramVal', 'diskBar', 'diskVal', 'batteryVal',
    'uptimeVal', 'activeApp', 'activeTitle', 'version', 'dataDir', 'orbCanvas',
    'orbCaption', 'miniOrb', 'hintRight',
  ];
  for (const id of ids) dom[id] = document.getElementById(id);
  dom.input = dom.input;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

function escapeAttr(value) {
  return escapeHtml(value);
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function formatTime(ts) {
  const date = ts ? new Date(Number(ts) * 1000) : new Date();
  return date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}

function formatDateTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

function formatUptime(seconds) {
  seconds = Number(seconds) || 0;
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days) return `${days}д ${hours}ч`;
  if (hours) return `${hours}ч ${minutes}м`;
  return `${minutes}м`;
}

function formatArgs(args) {
  if (!args || (typeof args === 'object' && !Object.keys(args).length)) return 'без аргументов';
  try {
    const json = JSON.stringify(args, null, 2);
    return json.length > 900 ? json.slice(0, 900) + '\n…' : json;
  } catch {
    return String(args);
  }
}

function toast(title, text = '', type = 'info', ms = 4500) {
  if (!dom.toasts) return;
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.innerHTML = `<b>${escapeHtml(title)}</b>${text ? escapeHtml(text) : ''}`;
  dom.toasts.appendChild(el);
  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transform = 'translateY(6px)';
    el.style.transition = '.25s';
    setTimeout(() => el.remove(), 300);
  }, ms);
}

async function api(path, options = {}) {
  const opts = { ...options, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } };
  const response = await fetch(path, opts);
  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || data.text || `HTTP ${response.status}`);
  }
  return data;
}

function showToolConfirm(ev) {
  return new Promise((resolve) => {
    let decided = false;
    const label = ev.label || TOOL_LABELS[ev.name] || ev.name || 'действие';
    const extra = confirmWarning(ev.name);
    const modal = openModal(`
      <div class="modal-head">
        <div style="display:flex;gap:12px;align-items:flex-start">
          <div class="confirm-icon">!</div>
          <div><h3>Требуется подтверждение</h3><p>Кванта хочет выполнить: <span class="confirm-tool">${escapeHtml(label)}</span></p></div>
        </div>
      </div>
      <div class="modal-body">
        <div class="confirm-args">${escapeHtml(formatArgs(ev.args))}</div>
        ${extra ? `<div class="confirm-warning">${escapeHtml(extra)}</div>` : ''}
      </div>
      <div class="modal-foot">
        <button type="button" class="btn danger" data-confirm="0">Отклонить</button>
        <button type="button" class="btn primary" data-confirm="1">Разрешить</button>
      </div>
    `, { dismissible: false, onClose: () => settle(false) });

    const settle = async (allow) => {
      if (decided) return;
      decided = true;
      const card = state.toolCards.get(ev.id);
      if (card) setToolStatus(card, allow ? 'pending' : 'denied');
      modal.close();
      try {
        await api('/api/confirm', {
          method: 'POST',
          body: JSON.stringify({ id: ev.id, allow }),
        });
      } catch (err) {
        toast('Подтверждение не доставлено', err.message, 'error');
      }
      resolve(allow);
    };

    modal.el.querySelectorAll('[data-confirm]').forEach((button) => {
      button.addEventListener('click', () => settle(button.dataset.confirm === '1'));
    });
    const card = state.toolCards.get(ev.id);
    if (card) setToolStatus(card, 'waiting');
    state.activeTask = `Ожидаю разрешение: ${label}`;
    setOrbState('permission');
  });
}

function confirmWarning(name) {
  if (['write_file', 'move_path', 'copy_path', 'delete_path'].includes(name)) {
    return 'Действие изменит файлы на диске. Удаление выполняется через Корзину.';
  }
  if (name === 'run_command') {
    return 'Команда выполнится в Windows от твоего пользователя. Проверь текст перед разрешением.';
  }
  if (['type_text', 'press_keys', 'mouse_click'].includes(name)) {
    return 'Кванта получит управление клавиатурой или мышью в активном окне.';
  }
  if (name === 'screenshot') {
    return 'Скриншот может содержать личные данные. Он сохранится локально в папке data/media.';
  }
  if (name === 'clipboard_get') {
    return 'В буфере обмена могут быть пароли или личные данные. Кванта увидит только текст и не сохранит его в заметки автоматически.';
  }
  if (name === 'clipboard_set') {
    return 'Текущее содержимое буфера обмена будет заменено.';
  }
  if (name === 'kill_process') {
    return 'Процесс будет завершён. Несохранённые данные в программе могут потеряться.';
  }
  return '';
}

/* ------------------------------------------------------------------ markdown */
function renderCodeBlock(lang, code) {
  return `<div class="code-block"><div class="code-head"><span>${escapeHtml(lang || 'code')}</span><button type="button">копировать</button></div><pre><code>${escapeHtml(code)}</code></pre></div>`;
}

function inlineMd(text) {
  const codes = [];
  text = text.replace(/`([^`]+)`/g, (_, code) => {
    codes.push(code);
    return `\u0001I${codes.length - 1}\u0001`;
  });
  text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
  text = text.replace(/~~([^~]+)~~/g, '<del>$1</del>');
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  text = text.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g,
    (match, prefix, url) => `${prefix}<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`);
  text = text.replace(/\u0001I(\d+)\u0001/g, (_, index) => `<code>${codes[Number(index)]}</code>`);
  return text;
}

function renderMarkdown(source) {
  if (!source) return '';
  const blocks = [];
  let text = String(source).replace(/```([\w+-]*)\r?\n?([\s\S]*?)```/g, (_, lang, code) => {
    blocks.push({ lang, code: code.replace(/\r?\n$/, '') });
    return `\u0000B${blocks.length - 1}\u0000`;
  });
  text = escapeHtml(text);
  const lines = text.split(/\r?\n/);
  const out = [];
  let para = [];
  let list = null;

  const closePara = () => {
    if (para.length) {
      out.push(`<p>${para.join('<br>')}</p>`);
      para = [];
    }
  };
  const closeList = () => {
    if (list) {
      out.push(`</${list}>`);
      list = null;
    }
  };

  for (const line of lines) {
    if (!line.trim()) {
      closePara();
      closeList();
      continue;
    }
    const blockMatch = line.match(/^\u0000B(\d+)\u0000$/);
    if (blockMatch) {
      closePara();
      closeList();
      out.push(`\u0000B${blockMatch[1]}\u0000`);
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      closePara();
      closeList();
      const level = heading[1].length;
      out.push(`<h${level}>${inlineMd(heading[2])}</h${level}>`);
      continue;
    }
    if (/^[-*_]{3,}\s*$/.test(line.trim())) {
      closePara();
      closeList();
      out.push('<hr>');
      continue;
    }
    const ul = line.match(/^\s*[-*]\s+(.*)$/);
    if (ul) {
      closePara();
      if (list !== 'ul') {
        closeList();
        out.push('<ul>');
        list = 'ul';
      }
      out.push(`<li>${inlineMd(ul[1])}</li>`);
      continue;
    }
    const ol = line.match(/^\s*\d+\.\s+(.*)$/);
    if (ol) {
      closePara();
      if (list !== 'ol') {
        closeList();
        out.push('<ol>');
        list = 'ol';
      }
      out.push(`<li>${inlineMd(ol[1])}</li>`);
      continue;
    }
    if (line.startsWith('&gt;')) {
      closePara();
      closeList();
      out.push(`<blockquote>${inlineMd(line.replace(/^&gt;\s?/, ''))}</blockquote>`);
      continue;
    }
    closeList();
    para.push(inlineMd(line));
  }
  closePara();
  closeList();
  return out.join('\n').replace(/\u0000B(\d+)\u0000/g, (_, index) => {
    const block = blocks[Number(index)];
    return block ? renderCodeBlock(block.lang, block.code) : '';
  });
}

/* ------------------------------------------------------------------ messages */
function isNearBottom() {
  if (!dom.chatScroll) return true;
  return dom.chatScroll.scrollHeight - dom.chatScroll.scrollTop - dom.chatScroll.clientHeight < 170;
}

function scrollToBottom(force = false) {
  if (!dom.chatScroll) return;
  if (!force && !isNearBottom()) return;
  requestAnimationFrame(() => {
    if (dom.chatScroll) dom.chatScroll.scrollTop = dom.chatScroll.scrollHeight;
  });
}

function hideWelcome() {
  if (dom.welcome) dom.welcome.classList.add('hidden');
}

function showWelcome() {
  if (dom.welcome) dom.welcome.classList.remove('hidden');
}

function appendUserBubble(text, ts) {
  hideWelcome();
  const el = document.createElement('div');
  el.className = 'msg user';
  const safe = escapeHtml(text).replace(/\n/g, '<br>');
  el.innerHTML = `<div class="msg-avatar">Я</div><div class="msg-body"><div class="bubble">${safe}</div><div class="msg-meta"><span>${formatTime(ts)}</span></div></div>`;
  dom.messages.appendChild(el);
  scrollToBottom(true);
}

function appendAssistantBubble(text, { stopped = false, ts = null } = {}) {
  if (!text || !String(text).trim()) return null;
  hideWelcome();
  const el = document.createElement('div');
  el.className = 'msg assistant';
  el.innerHTML = `<div class="msg-avatar">Q</div><div class="msg-body"><div class="bubble md">${renderMarkdown(text)}</div><div class="msg-meta"><span>${formatTime(ts)}</span>${stopped ? '<span class="stopped">остановлено</span>' : ''}</div></div>`;
  dom.messages.appendChild(el);
  return el;
}

function createAssistantPlaceholder() {
  hideWelcome();
  const el = document.createElement('div');
  el.className = 'msg assistant';
  const time = formatTime();
  el.innerHTML = `<div class="msg-avatar">Q</div><div class="msg-body"><div class="bubble md"><span class="typing"><i></i><i></i><i></i></span></div><div class="msg-meta"><span>${time}</span></div></div>`;
  dom.messages.appendChild(el);
  scrollToBottom();
  return {
    el,
    bubble: el.querySelector('.bubble'),
    meta: el.querySelector('.msg-meta'),
    raw: '',
    time,
    pending: false,
  };
}

function updateCurrentText(text) {
  const cur = state.current;
  if (!cur) return;
  cur.raw = text;
  if (cur.pending) return;
  cur.pending = true;
  requestAnimationFrame(() => {
    cur.pending = false;
    if (state.current === cur) {
      cur.bubble.innerHTML = renderMarkdown(cur.raw) + '<span class="stream-cursor">▋</span>';
      scrollToBottom();
    }
  });
}

function finalizeCurrent({ content = undefined, stopped = false } = {}) {
  const cur = state.current;
  if (!cur) return;
  state.current = null;
  const text = content !== undefined ? content : cur.raw;
  if (!text || !String(text).trim()) {
    cur.el.remove();
    return;
  }
  cur.bubble.innerHTML = renderMarkdown(text);
  cur.meta.innerHTML = `<span>${cur.time}</span>${stopped ? '<span class="stopped">остановлено</span>' : ''}`;
  scrollToBottom();
}

function showErrorBubble(message) {
  finalizeCurrent();
  hideWelcome();
  const el = document.createElement('div');
  el.className = 'msg assistant';
  el.innerHTML = `<div class="msg-avatar">Q</div><div class="msg-body"><div class="bubble md error-note"><b>Сбой связи с нейроядром.</b><br>${escapeHtml(message)}</div><div class="msg-meta"><span>${formatTime()}</span></div></div>`;
  dom.messages.appendChild(el);
  scrollToBottom(true);
}

/* ------------------------------------------------------------------ tools */
function setToolStatus(card, mode) {
  if (!card || !card.status) return;
  const status = card.status;
  if (mode === 'waiting') {
    status.className = 'tool-status';
    status.innerHTML = '<span class="tool-spinner"></span><span>ждёт подтверждения</span>';
  } else if (mode === 'pending') {
    status.className = 'tool-status';
    status.innerHTML = '<span class="tool-spinner"></span><span>выполняется…</span>';
  } else if (mode === 'denied') {
    status.className = 'tool-status fail';
    status.textContent = '✕ отклонено';
  } else if (mode === 'ok') {
    status.className = 'tool-status ok';
    status.textContent = '✓ готово';
  } else if (mode === 'fail') {
    status.className = 'tool-status fail';
    status.textContent = '✕ ошибка';
  }
}

function createToolCard(call) {
  const id = call.id || `tool_${Date.now()}_${Math.random().toString(16).slice(2)}`;
  if (state.toolCards.has(id)) return state.toolCards.get(id);
  const label = call.label || TOOL_LABELS[call.name] || call.name || 'Инструмент';
  const card = document.createElement('div');
  card.className = 'tool-card';
  card.dataset.id = id;
  card.innerHTML = `
    <div class="tool-head">
      <span class="tool-icon">◆</span>
      <span class="tool-label">${escapeHtml(label)}</span>
      <span class="tool-status"><span class="tool-spinner"></span><span>выполняется…</span></span>
      <button class="tool-toggle" type="button">детали</button>
    </div>
    <div class="tool-args"></div>
    <div class="tool-result"></div>
    <div class="tool-media"></div>`;
  card.querySelector('.tool-args').textContent = formatArgs(call.args || call.arguments || {});
  card.querySelector('.tool-toggle').addEventListener('click', () => card.classList.toggle('expanded'));
  dom.messages.appendChild(card);
  const entry = {
    el: card,
    status: card.querySelector('.tool-status'),
    result: card.querySelector('.tool-result'),
    media: card.querySelector('.tool-media'),
  };
  state.toolCards.set(id, entry);
  return entry;
}

function updateToolCard(card, result) {
  if (!card || !result) return;
  const ok = result.ok !== false;
  card.el.classList.toggle('ok', ok);
  card.el.classList.toggle('fail', !ok);
  const text = String(result.text || '');
  setToolStatus(card, ok ? 'ok' : (text.includes('отклонил') ? 'denied' : 'fail'));
  card.result.textContent = text || '(пусто)';
  card.media.innerHTML = '';
  for (const item of result.media || []) {
    if (!item || !item.url) continue;
    if (item.type === 'image') {
      const link = document.createElement('a');
      link.href = item.url;
      link.target = '_blank';
      link.rel = 'noopener';
      const img = document.createElement('img');
      img.src = item.url;
      img.alt = item.name || 'изображение';
      img.loading = 'lazy';
      img.addEventListener('load', () => scrollToBottom());
      link.appendChild(img);
      card.media.appendChild(link);
    } else {
      const link = document.createElement('a');
      link.className = 'file-link';
      link.href = item.url;
      link.target = '_blank';
      link.rel = 'noopener';
      if (item.name) link.download = item.name;
      link.textContent = item.name ? `⬇ ${item.name}` : '⬇ файл';
      card.media.appendChild(link);
    }
  }
  if (!ok || (result.media || []).length) card.el.classList.add('expanded');
  scrollToBottom();
}

function renderMessages() {
  if (!dom.messages) return;
  dom.messages.innerHTML = '';
  state.toolCards.clear();
  state.current = null;
  if (!state.messages || !state.messages.length) {
    showWelcome();
    return;
  }
  hideWelcome();
  state.messages.forEach((msg) => {
    if (msg.role === 'user') {
      appendUserBubble(msg.content || '', msg.ts);
    } else if (msg.role === 'assistant') {
      if (msg.content && String(msg.content).trim()) {
        appendAssistantBubble(msg.content, { stopped: !!msg.stopped, ts: msg.ts });
      }
      for (const call of msg.tool_calls || []) {
        createToolCard({
          id: call.id,
          name: call.name,
          args: call.arguments,
        });
      }
    } else if (msg.role === 'tool') {
      const id = msg.tool_call_id || `tool_${msg.name || 'result'}`;
      let card = state.toolCards.get(id);
      if (!card) card = createToolCard({ id, name: msg.name });
      updateToolCard(card, {
        ok: msg.ok !== false,
        text: msg.content || '',
        media: msg.media || [],
      });
    }
  });
  scrollToBottom(true);
}

/* ------------------------------------------------------------------ streaming */
function updateBusyUI() {
  if (!dom.sendBtn || !dom.stopBtn) return;
  dom.sendBtn.classList.toggle('hidden', state.busy);
  dom.stopBtn.classList.toggle('hidden', !state.busy);
  dom.sendBtn.disabled = state.busy;
}

function setModeChip(mode) {
  if (!dom.modeChip) return;
  dom.modeChip.textContent = MODE_LABELS[mode] || MODE_LABELS.smart;
  dom.modeChip.className = `mode-chip ${mode === 'safe' ? 'safe' : mode === 'auto' ? 'auto' : ''}`;
}

function setChatTitle(title) {
  state.chatTitle = title || 'Новый чат';
  if (dom.chatTitle) dom.chatTitle.textContent = state.chatTitle;
}

function rememberChatUrl(chatId) {
  try {
    const url = new URL(location.href);
    if (chatId) url.searchParams.set('chat', chatId);
    else url.searchParams.delete('chat');
    history.replaceState(null, '', url);
  } catch { /* ignore */ }
}

async function handleStreamEvent(ev) {
  switch (ev.type) {
    case 'start':
      state.chatId = ev.chat_id;
      setChatTitle(ev.title || 'Новый чат');
      rememberChatUrl(state.chatId);
      try { localStorage.setItem('quanta:chat', state.chatId); } catch { /* ignore */ }
      hideWelcome();
      refreshChats();
      break;
    case 'user_saved':
      if (ev.title) setChatTitle(ev.title);
      break;
    case 'step_start':
      if (state.current) finalizeCurrent();
      state.current = createAssistantPlaceholder();
      state.activeTask = 'Формирую ответ';
      setOrbState('thinking');
      break;
    case 'delta':
      if (!state.current) state.current = createAssistantPlaceholder();
      updateCurrentText((state.current.raw || '') + (ev.text || ''));
      appendStreamingSpeechDelta(ev.text || '');
      scrollToBottom();
      break;
    case 'step_end': {
      const msg = ev.message || {};
      if (msg.content !== undefined || (msg.tool_calls || []).length) {
        state.messages.push(msg);
      }
      finalizeCurrent({ content: msg.content || '', stopped: !!ev.stopped });
      setOrbState((msg.tool_calls || []).length ? 'tool' : 'thinking');
      break;
    }
    case 'tool_call': {
      stopSpeech();
      finalizeCurrent();
      const card = createToolCard(ev);
      state.activeTask = ev.confirm
        ? `Нужно разрешение: ${ev.label || TOOL_LABELS[ev.name] || ev.name}`
        : (ev.label || TOOL_LABELS[ev.name] || 'Выполняю действие');
      if (!ev.allowed) setToolStatus(card, 'fail');
      else if (ev.confirm) setToolStatus(card, 'waiting');
      else setToolStatus(card, 'pending');
      setOrbState('tool');
      break;
    }
    case 'tool_result': {
      const card = state.toolCards.get(ev.id);
      if (card) updateToolCard(card, ev);
      state.messages.push({
        role: 'tool',
        tool_call_id: ev.id,
        name: ev.name,
        content: ev.text || '',
        ok: !!ev.ok,
        media: ev.media || [],
        ts: Date.now() / 1000,
      });
      state.activeTask = 'Действие завершено';
      setOrbState('thinking');
      break;
    }
    case 'confirm':
      showToolConfirm(ev);
      break;
    case 'done':
      finalizeCurrent();
      state.busy = false;
      updateBusyUI();
      state.activeTask = '';
      setOrbState('idle');
      if (ev.title) setChatTitle(ev.title);
      if (ev.limit) toast('Лимит шагов', 'Кванта остановилась, чтобы не уйти в бесконечный цикл.', 'warn');
      refreshChats();
      if (ev.message && String(ev.message.content || '').trim()) {
        await speakAssistantResponse(String(ev.message.content || ''));
      }
      break;
    case 'stopped':
      finalizeCurrent({ stopped: true });
      stopSpeech();
      state.busy = false;
      updateBusyUI();
      state.activeTask = '';
      setOrbState('idle');
      break;
    case 'error':
      stopSpeech();
      finalizeCurrent();
      showErrorBubble(ev.message || 'Неизвестная ошибка');
      toast('Ошибка', ev.message || 'Неизвестная ошибка', 'error');
      state.busy = false;
      updateBusyUI();
      state.activeTask = '';
      setOrbState('idle');
      break;
    default:
      break;
  }
}

async function sendMessage() {
  if (state.busy || !state.settings) return;
  const text = dom.input.value.trim();
  if (!text) return;
  dom.input.value = '';
  autoGrowInput();
  closeSidebar();
  stopSpeech();
  appendUserBubble(text);
  state.messages.push({ role: 'user', content: text, ts: Date.now() / 1000 });
  state.busy = true;
  state.activeTask = 'Формирую ответ';
  updateBusyUI();
  setOrbState('thinking');
  const controller = new AbortController();
  state.abort = controller;
  let finished = false;
  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: state.chatId, text, tone_mode: state.seriousMode ? 'serious' : 'default' }),
      signal: controller.signal,
    });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const data = await response.json();
        detail = data.error || data.text || detail;
      } catch { /* ignore */ }
      throw new Error(detail);
    }
    if (!response.body) throw new Error('Сервер не прислал поток ответа.');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try {
          const ev = JSON.parse(trimmed);
          if (ev.type === 'done' || ev.type === 'error' || ev.type === 'stopped') finished = true;
          await handleStreamEvent(ev);
        } catch (err) {
          console.warn('Bad stream event', trimmed, err);
        }
      }
    }
    if (buffer.trim()) {
      try {
        const ev = JSON.parse(buffer.trim());
        if (ev.type === 'done' || ev.type === 'error' || ev.type === 'stopped') finished = true;
        await handleStreamEvent(ev);
      } catch { /* ignore */ }
    }
    if (!finished) {
      stopSpeech();
      finalizeCurrent();
      if (state.busy) {
        state.busy = false;
        updateBusyUI();
        setOrbState('idle');
      }
    }
  } catch (err) {
    stopSpeech();
    if (err.name === 'AbortError') {
      finalizeCurrent({ stopped: true });
    } else {
      showErrorBubble(err.message || String(err));
      toast('Не удалось получить ответ', err.message || String(err), 'error');
    }
    state.busy = false;
    updateBusyUI();
    setOrbState('idle');
  } finally {
    state.abort = null;
  }
}

async function stopGeneration() {
  const wasBusy = state.busy;
  stopSpeech();
  if (state.abort) {
    try { state.abort.abort(); } catch { /* ignore */ }
  }
  if (state.chatId) {
    try {
      await api('/api/stop', { method: 'POST', body: JSON.stringify({ chat_id: state.chatId }) });
    } catch { /* ignore */ }
  }
  finalizeCurrent({ stopped: true });
  state.busy = false;
  updateBusyUI();
  setOrbState('idle');
  if (wasBusy) toast('Остановлено', 'Генерация прервана.', 'warn', 2200);
  setTimeout(() => syncCurrentChat(true), 1000);
}

/* ------------------------------------------------------------------ chats */
async function refreshChats() {
  try {
    const data = await api('/api/chats');
    state.chats = data.chats || [];
    renderChats();
    if (state.chatId) {
      const chat = state.chats.find((c) => c.id === state.chatId);
      if (chat && chat.title) setChatTitle(chat.title);
    }
  } catch { /* server may be busy; ignore */ }
}

function renderChats() {
  if (!dom.chatList) return;
  dom.chatList.innerHTML = '';
  if (!state.chats.length) {
    const empty = document.createElement('div');
    empty.className = 'empty-chats';
    empty.textContent = 'Пока нет чатов';
    dom.chatList.appendChild(empty);
    return;
  }
  state.chats.forEach((chat) => {
    const item = document.createElement('div');
    item.className = `chat-item${chat.id === state.chatId ? ' active' : ''}`;
    item.dataset.id = chat.id;
    item.tabIndex = 0;
    item.setAttribute('role', 'button');
    item.title = chat.title || 'Новый чат';
    item.innerHTML = `<span class="chat-item-title">${escapeHtml(chat.title || 'Новый чат')}</span><button class="chat-del" type="button" title="Удалить чат">×</button>`;
    item.addEventListener('click', (event) => {
      if (event.target.closest('.chat-del')) {
        event.stopPropagation();
        deleteChat(chat.id);
      } else {
        openChat(chat.id);
      }
    });
    item.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openChat(chat.id);
      }
    });
    dom.chatList.appendChild(item);
  });
}

async function newChat() {
  if (state.busy) await stopGeneration();
  state.chatId = null;
  state.messages = [];
  state.current = null;
  state.toolCards.clear();
  setChatTitle('Новый чат');
  rememberChatUrl(null);
  try { localStorage.removeItem('quanta:chat'); } catch { /* ignore */ }
  showWelcome();
  renderMessages();
  renderChats();
  setOrbState('idle');
  closeSidebar();
  setTimeout(() => dom.input?.focus(), 50);
}

async function openChat(chatId, { silent = false } = {}) {
  if (!chatId) return;
  if (state.busy) await stopGeneration();
  try {
    const data = await api(`/api/chats/${encodeURIComponent(chatId)}`);
    const chat = data.chat;
    state.chatId = chat.id;
    state.messages = chat.messages || [];
    setChatTitle(chat.title || 'Новый чат');
    try { localStorage.setItem('quanta:chat', chat.id); } catch { /* ignore */ }
    rememberChatUrl(chat.id);
    renderMessages();
    renderChats();
    setOrbState('idle');
    if (!silent) {
      closeSidebar();
      setTimeout(() => dom.input?.focus(), 50);
    }
  } catch (err) {
    toast('Чат не открыт', err.message, 'error');
    await refreshChats();
  }
}

async function deleteChat(chatId) {
  const chat = state.chats.find((c) => c.id === chatId);
  const ok = await confirmDialog({
    title: 'Удалить чат?',
    text: `«${chat?.title || 'Этот чат'}» исчезнет из истории. Заметки и напоминания останутся.`,
    confirmLabel: 'Удалить',
    danger: true,
  });
  if (!ok) return;
  try {
    await api(`/api/chats/${encodeURIComponent(chatId)}`, { method: 'DELETE' });
    if (state.chatId === chatId) await newChat();
    else await refreshChats();
    toast('Чат удалён', '', 'success', 2200);
  } catch (err) {
    toast('Не удалось удалить чат', err.message, 'error');
  }
}

async function syncCurrentChat(force = false) {
  if (!state.chatId || state.busy) return;
  try {
    const data = await api(`/api/chats/${encodeURIComponent(state.chatId)}`);
    const chat = data.chat;
    if (chat.title) setChatTitle(chat.title);
    if (force || (chat.messages || []).length !== state.messages.length) {
      state.messages = chat.messages || [];
      renderMessages();
    }
  } catch {
    /* chat may have been deleted in another window */
  }
}

/* ------------------------------------------------------------------ settings/mode */
function applySettingsToUI() {
  if (!state.settings) return;
  const settings = state.settings;
  if (dom.resourceProfileSelect) dom.resourceProfileSelect.value = settings.resource_profile || 'work';
  if (dom.accessSelect) dom.accessSelect.value = settings.access_mode || 'smart';
  setModeChip(settings.access_mode || 'smart');
  if (dom.version) dom.version.textContent = state.version || '0.1.0';
  if (dom.dataDir) dom.dataDir.textContent = state.dataDir || 'data';
  if (dom.hintRight) dom.hintRight.textContent = settings.model || '';
  renderModelSelect();
  document.title = 'Кванта';
}

async function saveSettings(patch, { silent = false } = {}) {
  try {
    const data = await api('/api/settings', {
      method: 'POST',
      body: JSON.stringify(patch),
    });
    state.settings = data.settings;
    applySettingsToUI();
    if (!silent) toast('Настройки сохранены', '', 'success', 2200);
    return state.settings;
  } catch (err) {
    if (!silent) toast('Настройки не сохранены', err.message, 'error');
    throw err;
  }
}

function setSeriousMode(enabled, { persist = true } = {}) {
  state.seriousMode = Boolean(enabled);
  document.body.dataset.tone = state.seriousMode ? 'serious' : 'natural';
  if (dom.seriousModeBtn) {
    dom.seriousModeBtn.classList.toggle('active', state.seriousMode);
    dom.seriousModeBtn.setAttribute('aria-pressed', String(state.seriousMode));
    dom.seriousModeBtn.title = state.seriousMode ? 'Выключить серьёзный режим' : 'Включить серьёзный режим';
  }
  if (persist) {
    try { localStorage.setItem('quanta:tone', state.seriousMode ? 'serious' : 'natural'); } catch { /* storage may be disabled */ }
  }
}

function setMode(mode, { persist = true } = {}) {
  mode = ['mini', 'standard', 'hud'].includes(mode) ? mode : 'standard';
  state.mode = mode;
  document.body.dataset.mode = mode;
  document.body.dataset.orb = state.orbState;
  $$('.view-switch [data-view]').forEach((button) => {
    button.classList.toggle('active', button.dataset.view === mode);
  });
  try { localStorage.setItem('quanta:view', mode); } catch { /* ignore */ }
  if (persist && state.settings && state.settings.ui_mode !== mode) {
    saveSettings({ ui_mode: mode }, { silent: true }).catch(() => {});
  }
  if (mode === 'hud') setTimeout(() => scrollToBottom(), 50);
}

function closeSidebar() {
  dom.app?.classList.remove('sidebar-open');
}

/* ------------------------------------------------------------------ models */
function renderModelSelect() {
  if (!dom.modelSelect) return;
  const current = state.settings?.model || '';
  const names = [];
  for (const model of state.models) {
    const name = typeof model === 'string' ? model : model.name;
    if (name && !names.includes(name)) names.push(name);
  }
  if (current && !names.includes(current)) names.unshift(current);
  if (!names.length) names.push(current || 'qwen3:4b-instruct');
  dom.modelSelect.innerHTML = names
    .map((name) => `<option value="${escapeAttr(name)}"${name === current ? ' selected' : ''}>${escapeHtml(name)}</option>`)
    .join('');
  const datalist = document.getElementById('modelOptions');
  if (datalist) {
    datalist.innerHTML = names.map((name) => `<option value="${escapeAttr(name)}"></option>`).join('');
  }
}

async function loadModels(showError = false) {
  try {
    const data = await api('/api/models');
    state.models = data.models || [];
    renderModelSelect();
    return true;
  } catch (err) {
    if (showError) toast('Модели недоступны', err.message, 'error');
    renderModelSelect();
    return false;
  }
}

/* ------------------------------------------------------------------ modal */
function closeModals() {
  const closers = modalClosers;
  modalClosers = [];
  closers.forEach((close) => {
    try { close(); } catch { /* ignore */ }
  });
}

function openModal(html, { wide = false, dismissible = true, onClose = null } = {}) {
  closeModals();
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop';
  backdrop.innerHTML = `<div class="modal${wide ? ' wide' : ''}">${html}</div>`;
  dom.modalRoot.appendChild(backdrop);
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    const index = modalClosers.indexOf(close);
    if (index >= 0) modalClosers.splice(index, 1);
    backdrop.remove();
    if (onClose) onClose();
  };
  modalClosers.push(close);
  if (dismissible) {
    backdrop.addEventListener('mousedown', (event) => {
      if (event.target === backdrop) close();
    });
  }
  backdrop.querySelectorAll('[data-close]').forEach((button) => {
    button.addEventListener('click', close);
  });
  return { el: backdrop, close };
}

function confirmDialog({ title, text, confirmLabel = 'Подтвердить', danger = false }) {
  return new Promise((resolve) => {
    let decided = false;
    const modal = openModal(`
      <div class="modal-head"><h3>${escapeHtml(title)}</h3></div>
      <div class="modal-body"><div class="form-note" style="font-size:12px;color:var(--muted)">${escapeHtml(text)}</div></div>
      <div class="modal-foot">
        <button type="button" class="btn" data-close>Отмена</button>
        <button type="button" class="btn ${danger ? 'danger' : 'primary'}" id="confirmYes">${escapeHtml(confirmLabel)}</button>
      </div>
    `, {
      dismissible: true,
      onClose: () => {
        if (!decided) {
          decided = true;
          resolve(false);
        }
      },
    });
    const yes = modal.el.querySelector('#confirmYes');
    yes?.addEventListener('click', () => {
      if (decided) return;
      decided = true;
      modal.close();
      resolve(true);
    });
  });
}

/* ------------------------------------------------------------------ drawer */
function closeDrawer() {
  state.drawerMode = '';
  dom.drawer?.classList.add('hidden');
}

function openDrawer(kind) {
  if (kind === 'settings') {
    openSettings();
    return;
  }
  state.drawerMode = kind;
  dom.drawer.classList.remove('hidden');
  dom.drawerBody.scrollTop = 0;
  if (kind === 'notes') renderNotesDrawer();
  else if (kind === 'reminders') renderRemindersDrawer();
  else if (kind === 'memory') refreshMemory();
  else if (kind === 'projects') refreshProjects();
  else if (kind === 'workflows') refreshWorkflows();
  else if (kind === 'actions') refreshActions();
  else if (kind === 'diagnostics') renderDiagnosticsDrawer();
  else if (kind === 'analytics') { renderAnalyticsDrawer(); pollSystem(); }
  closeSidebar();
}

function noteCard(note) {
  const preview = String(note.text || '').replace(/\s+/g, ' ').slice(0, 260);
  return `<div class="note-card">
    <div class="note-title">${escapeHtml(note.title || 'Без названия')}</div>
    <div class="note-text">${escapeHtml(preview)}${(note.text || '').length > 260 ? '…' : ''}</div>
    <div class="note-meta"><span>${formatDateTime(note.updated)}</span>
      <div class="row-actions">
        <button type="button" data-action="edit-note" data-id="${escapeAttr(note.id)}">✎</button>
        <button type="button" class="danger" data-action="delete-note" data-id="${escapeAttr(note.id)}">×</button>
      </div>
    </div>
  </div>`;
}

function renderNotesDrawer() {
  state.drawerMode = 'notes';
  dom.drawerTitle.textContent = 'Заметки';
  const cards = state.notes.length
    ? state.notes.map(noteCard).join('')
    : '<div class="empty-state"><b>Заметок пока нет</b>Попроси Кванту «запомни…» или создай вручную.</div>';
  dom.drawerBody.innerHTML = `
    <div class="drawer-actions">
      <button type="button" class="btn primary small" data-action="new-note">+ Новая заметка</button>
      <button type="button" class="btn small" data-action="refresh-notes">Обновить</button>
    </div>
    <div class="form-note" style="margin-bottom:12px">Локальные заметки. Кванта видит их и может дополнять.</div>
    ${cards}`;
}

function defaultReminderValue() {
  const date = new Date(Date.now() + 3600000);
  date.setSeconds(0, 0);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

function reminderCard(reminder) {
  const due = reminder.due_ts ? new Date(Number(reminder.due_ts) * 1000) : new Date(reminder.due || 0);
  const overdue = !reminder.done && due.getTime() < Date.now();
  return `<div class="reminder-card${reminder.done ? ' done' : ''}${overdue ? ' overdue' : ''}">
    <div class="reminder-time">${formatDateTime(due)}${overdue ? ' · просрочено' : ''}</div>
    <div class="reminder-text">${escapeHtml(reminder.text || '')}</div>
    <div class="reminder-meta">
      <div class="row-actions">
        <button type="button" data-action="done-reminder" data-id="${escapeAttr(reminder.id)}" title="Отметить выполненным">✓</button>
        <button type="button" class="danger" data-action="delete-reminder" data-id="${escapeAttr(reminder.id)}" title="Удалить">×</button>
      </div>
    </div>
  </div>`;
}

function renderRemindersDrawer() {
  state.drawerMode = 'reminders';
  dom.drawerTitle.textContent = 'Напоминания';
  const cards = state.reminders.length
    ? state.reminders.map(reminderCard).join('')
    : '<div class="empty-state"><b>Активных напоминаний нет</b>Кванта разбудит тебя вовремя.</div>';
  dom.drawerBody.innerHTML = `
    <form id="reminderForm" class="reminder-form">
      <div class="field"><label>О чём напомнить</label><input id="reminderText" maxlength="500" placeholder="Например: сделать перерыв" required></div>
      <div class="field"><label>Когда</label><input type="datetime-local" id="reminderWhen" value="${defaultReminderValue()}" required></div>
      <button type="submit" class="btn primary">Создать напоминание</button>
    </form>
    <div class="settings-section">
      <h4>Активные</h4>
      ${cards}
    </div>
    <button type="button" class="btn small" data-action="refresh-reminders">Обновить список</button>`;
}

function memoryCard(item) {
  return '<article class="memory-card"><div class="memory-text">' + escapeHtml(item.text || '') +
    '</div><div class="memory-meta"><span>' + escapeHtml(formatDateTime(item.updated)) +
    '</span><button class="text-danger" data-action="delete-memory" data-id="' + escapeAttr(item.id) +
    '" type="button">Забыть</button></div></article>';
}

async function refreshMemory() {
  try {
    const data = await api('/api/memory');
    state.memories = data.memory || [];
    if (state.drawerMode === 'memory') renderMemoryDrawer();
  } catch (err) {
    if (state.drawerMode === 'memory') {
      dom.drawerTitle.textContent = 'Память';
      dom.drawerBody.innerHTML = '<div class="empty-state">Не удалось загрузить память: ' + escapeHtml(err.message) + '</div>';
    }
  }
}

function renderMemoryDrawer() {
  state.drawerMode = 'memory';
  dom.drawerTitle.textContent = 'Память Кванты';
  const cards = state.memories.length
    ? state.memories.map(memoryCard).join('')
    : '<div class="empty-state"><b>Пока пусто</b>Добавляй только то, что Кванте действительно стоит помнить.</div>';
  dom.drawerBody.innerHTML =
    '<form id="memoryForm" class="reminder-form">' +
      '<div class="field"><label>Что запомнить</label><textarea id="memoryText" maxlength="600" placeholder="Например: люблю короткие ответы и быстрый темп" required></textarea></div>' +
      '<button type="submit" class="btn primary">Добавить в память</button>' +
    '</form>' +
    '<div class="form-note" style="margin:10px 0 14px">Записи хранятся локально и попадают в контекст новых чатов. Не сохраняй пароли и секреты. Можно удалить любую запись кнопкой «Забыть».</div>' +
    '<div class="memory-list">' + cards + '</div>';
}

function projectStatusLabel(status) {
  return ({ active: 'В работе', paused: 'На паузе', done: 'Готово', archived: 'В архиве' })[status] || 'В работе';
}

function projectCard(project) {
  const linkedIds = project.chat_ids || [];
  const linked = Boolean(state.chatId && linkedIds.includes(state.chatId));
  const currentProject = state.projects.find((item) =>
    item.id !== project.id && (item.chat_ids || []).includes(state.chatId)
  );
  const chats = project.chats || [];
  const files = project.files || [];
  const decisions = project.decisions || [];
  const archived = project.status === 'archived';
  return `<article class="project-card${archived ? ' archived' : ''}">
    <div class="project-card-head">
      <b>${escapeHtml(project.title || 'Проект')}</b>
      <span class="project-status ${escapeAttr(project.status || 'active')}">${projectStatusLabel(project.status)}</span>
    </div>
    <div class="project-field"><small>ЦЕЛЬ</small><div>${escapeHtml(project.goal || 'Не задана')}</div></div>
    <div class="project-field next"><small>СЛЕДУЮЩИЙ ШАГ</small><div>${escapeHtml(project.next_step || 'Пока не определён')}</div></div>
    ${project.summary ? `<div class="project-field"><small>КОНТЕКСТ</small><div>${escapeHtml(project.summary)}</div></div>` : ''}
    ${decisions.length ? `<div class="project-field"><small>РЕШЕНИЯ</small><ul>${decisions.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul></div>` : ''}
    ${files.length ? `<div class="project-field"><small>ФАЙЛЫ</small><ul>${files.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul></div>` : ''}
    <div class="project-field"><small>ПРИВЯЗАННЫЕ ЧАТЫ</small>
      ${chats.length ? `<div class="project-chat-links">${chats.map((chat) => `<button type="button" data-action="open-project-chat" data-chat-id="${escapeAttr(chat.id)}">${escapeHtml(chat.title || 'Чат')}</button>`).join('')}</div>` : '<div>Чатов пока нет.</div>'}
    </div>
    <div class="project-card-actions">
      <button type="button" class="btn small" data-action="edit-project" data-id="${escapeAttr(project.id)}">Изменить</button>
      ${state.chatId && (!archived || linked) ? `<button type="button" class="btn small ${linked ? '' : 'primary'}" data-action="${linked ? 'unlink-project-chat' : 'link-project-chat'}" data-id="${escapeAttr(project.id)}">${linked ? 'Отвязать этот чат' : currentProject ? 'Перенести чат сюда' : 'Привязать этот чат'}</button>` : ''}
      <button type="button" class="btn small" data-action="${archived ? 'restore-project' : 'archive-project'}" data-id="${escapeAttr(project.id)}">${archived ? 'Вернуть в работу' : 'В архив'}</button>
    </div>
    <div class="project-meta">Обновлён ${formatDateTime(project.updated)}</div>
  </article>`;
}

function renderProjectForm(project = null) {
  const editing = Boolean(project);
  const current = project || {};
  return `<form id="projectForm" class="reminder-form">
    <input type="hidden" id="projectId" value="${escapeAttr(current.id || '')}">
    <div class="project-form-heading"><b>${editing ? 'Изменить проект' : 'Новый проект'}</b>${editing ? '<button type="button" class="btn small" data-action="cancel-project-edit">Отмена</button>' : ''}</div>
    <div class="field"><label for="projectTitle">Название</label><input id="projectTitle" maxlength="120" value="${escapeAttr(current.title || '')}" placeholder="Например: Развитие Кванты" required></div>
    <div class="field"><label for="projectGoal">Цель</label><textarea id="projectGoal" maxlength="1200" rows="2" placeholder="Какой результат хотим получить?" required>${escapeHtml(current.goal || '')}</textarea></div>
    <div class="field"><label for="projectNextStep">Следующий шаг</label><input id="projectNextStep" maxlength="1000" value="${escapeAttr(current.next_step || '')}" placeholder="Что делаем дальше?"></div>
    <div class="field"><label for="projectSummary">Текущий контекст</label><textarea id="projectSummary" maxlength="3000" rows="3" placeholder="Коротко: где остановились и что важно не потерять">${escapeHtml(current.summary || '')}</textarea></div>
    <div class="field"><label for="projectDecisions">Решения — по одному на строку</label><textarea id="projectDecisions" rows="3" placeholder="Например: локальные данные остаются на компьютере">${escapeHtml((current.decisions || []).join('\n'))}</textarea></div>
    <div class="field"><label for="projectFiles">Связанные пути к файлам — по одному на строку</label><textarea id="projectFiles" rows="2" placeholder="C:\\Projects\\...">${escapeHtml((current.files || []).join('\n'))}</textarea></div>
    <div class="field"><label for="projectStatus">Статус</label><select id="projectStatus">
      ${['active', 'paused', 'done', 'archived'].map((status) => `<option value="${status}"${(current.status || 'active') === status ? ' selected' : ''}>${projectStatusLabel(status)}</option>`).join('')}
    </select></div>
    ${!editing && state.chatId ? '<label class="project-link-check"><input id="projectLinkCurrentChat" type="checkbox" checked> Привязать к текущему чату</label>' : ''}
    <button type="submit" class="btn primary">${editing ? 'Сохранить проект' : 'Создать проект'}</button>
  </form>`;
}

async function refreshProjects() {
  try {
    const data = await api('/api/projects?all=1');
    state.projects = data.projects || [];
    if (state.drawerMode === 'projects') renderProjectsDrawer();
  } catch (err) {
    if (state.drawerMode === 'projects') {
      dom.drawerTitle.textContent = 'Проекты';
      dom.drawerBody.innerHTML = `<div class="empty-state">Не удалось загрузить проекты: ${escapeHtml(err.message)}</div>`;
    }
  }
}

function renderProjectsDrawer() {
  state.drawerMode = 'projects';
  dom.drawerTitle.textContent = 'Проекты';
  const showForm = Boolean(state.editingProjectId);
  const editing = state.projects.find((item) => item.id === state.editingProjectId) || null;
  const visible = state.projects;
  const cards = visible.length
    ? visible.map(projectCard).join('')
    : '<div class="empty-state"><b>Пока нет проектов</b>Создай карточку здесь или попроси Кванту вести многошаговую задачу как проект.</div>';
  dom.drawerBody.innerHTML = `<div class="project-drawer-intro">Цели, решения и следующий шаг сохраняются между привязанными чатами.</div>
    <div class="drawer-actions project-list-head"><b>Все проекты</b><button type="button" class="btn small primary" data-action="new-project">+ Новый проект</button></div>
    ${showForm ? renderProjectForm(editing) : ''}
    <div class="project-list">${cards}</div>`;
}

async function saveProjectForm(form) {
  const projectId = form.querySelector('#projectId')?.value || '';
  const payload = {
    title: form.querySelector('#projectTitle')?.value.trim() || '',
    goal: form.querySelector('#projectGoal')?.value.trim() || '',
    next_step: form.querySelector('#projectNextStep')?.value.trim() || '',
    summary: form.querySelector('#projectSummary')?.value.trim() || '',
    decisions: (form.querySelector('#projectDecisions')?.value || '').split('\n').map((item) => item.trim()).filter(Boolean),
    files: (form.querySelector('#projectFiles')?.value || '').split('\n').map((item) => item.trim()).filter(Boolean),
    status: form.querySelector('#projectStatus')?.value || 'active',
  };
  if (!payload.title || !payload.goal) return;
  try {
    if (projectId) {
      await api(`/api/projects/${encodeURIComponent(projectId)}`, { method: 'PUT', body: JSON.stringify(payload) });
      toast('Проект обновлён', payload.title, 'success', 2200);
    } else {
      if (form.querySelector('#projectLinkCurrentChat')?.checked && state.chatId) payload.chat_id = state.chatId;
      await api('/api/projects', { method: 'POST', body: JSON.stringify(payload) });
      toast('Проект создан', payload.title, 'success', 2200);
    }
    state.editingProjectId = '';
    await refreshProjects();
    await refreshChats();
  } catch (err) { toast('Не удалось сохранить проект', err.message, 'error'); }
}

async function linkProjectChat(projectId, linked) {
  if (!state.chatId) {
    toast('Сначала открой чат', 'Привязать можно текущий чат.', 'warn', 2400);
    return;
  }
  const currentProject = state.projects.find((item) =>
    item.id !== projectId && (item.chat_ids || []).includes(state.chatId)
  );
  if (linked && currentProject) {
    const approved = await confirmDialog({
      title: 'Перенести чат в другой проект?',
      text: `Чат отвяжется от проекта «${currentProject.title}» и будет связан с выбранным проектом.`,
      confirmLabel: 'Перенести',
    });
    if (!approved) return;
  }
  try {
    await api(`/api/projects/${encodeURIComponent(projectId)}/${linked ? 'link' : 'unlink'}`, {
      method: 'POST', body: JSON.stringify({ chat_id: state.chatId }),
    });
    await refreshProjects();
    toast(linked ? 'Чат привязан к проекту' : 'Чат отвязан от проекта', '', 'success', 2200);
  } catch (err) { toast('Не удалось изменить привязку', err.message, 'error'); }
}

async function setProjectArchived(projectId, archived) {
  try {
    await api(`/api/projects/${encodeURIComponent(projectId)}/${archived ? 'archive' : 'restore'}`, { method: 'POST', body: '{}' });
    await refreshProjects();
    toast(archived ? 'Проект убран в архив' : 'Проект возвращён', '', 'success', 2200);
  } catch (err) { toast('Не удалось изменить статус проекта', err.message, 'error'); }
}

async function openProjectChat(chatId) {
  if (!chatId) return;
  closeDrawer();
  await openChat(chatId);
}

function workflowCard(item) {
  const builtin = Boolean(item.builtin);
  return '<article class="workflow-card"><div class="workflow-card-head"><b>' + escapeHtml(item.title || 'Сценарий') +
    '</b><span class="memory-tag">' + (builtin ? 'встроенный' : 'мой') + '</span></div><div class="workflow-prompt">' +
    escapeHtml(item.prompt || '') + '</div><div class="row-actions">' +
    '<button type="button" class="btn primary small" data-action="run-workflow" data-id="' + escapeAttr(item.id) + '">Запустить</button>' +
    (builtin ? '' : '<button type="button" class="text-danger" data-action="delete-workflow" data-id="' + escapeAttr(item.id) + '">Удалить</button>') +
    '</div></article>';
}

function renderWorkflowQuickActions() {
  const target = document.getElementById('workflowQuickActions');
  if (!target) return;
  target.innerHTML = state.workflows.slice(0, 5).map((item) =>
    '<button data-prompt="' + escapeAttr(item.prompt) + '" title="' + escapeAttr(item.title) + '">' +
    escapeHtml(item.title) + '</button>'
  ).join('');
}

async function refreshWorkflows() {
  try {
    const data = await api('/api/workflows');
    state.workflows = data.workflows || [];
    renderWorkflowQuickActions();
    if (state.drawerMode === 'workflows') renderWorkflowsDrawer();
  } catch (err) {
    if (state.drawerMode === 'workflows') {
      dom.drawerTitle.textContent = 'Сценарии';
      dom.drawerBody.innerHTML = '<div class="empty-state">Не удалось загрузить сценарии: ' + escapeHtml(err.message) + '</div>';
    }
  }
}

function renderWorkflowsDrawer() {
  state.drawerMode = 'workflows';
  dom.drawerTitle.textContent = 'Сценарии';
  const cards = state.workflows.length
    ? state.workflows.map(workflowCard).join('')
    : '<div class="empty-state">Сценариев пока нет.</div>';
  dom.drawerBody.innerHTML =
    '<form id="workflowForm" class="reminder-form">' +
      '<div class="field"><label>Название</label><input id="workflowTitle" maxlength="80" placeholder="Например: Подготовка к работе" required></div>' +
      '<div class="field"><label>Инструкция для Кванты</label><textarea id="workflowPrompt" maxlength="2000" placeholder="Опиши, что нужно сделать и в каком порядке" required></textarea></div>' +
      '<button type="submit" class="btn primary">Сохранить сценарий</button>' +
    '</form>' +
    '<div class="form-note" style="margin:10px 0 14px">Сценарий подставляет задачу в чат. Действия с файлами и компьютером по-прежнему проходят через выбранный режим доступа.</div>' +
    '<div class="workflow-list">' + cards + '</div>';
}

function actionCard(item) {
  const stamp = new Date(Number(item.ts || 0) * 1000).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' });
  const canUndo = Boolean(item.undo && item.ok && !item.undone);
  return '<article class="action-card ' + (item.ok ? 'success' : 'failed') + '">' +
    '<div class="action-card-head"><b>' + escapeHtml(item.label || 'Действие') + '</b><span>' +
    (item.undone ? 'отменено' : item.ok ? 'готово' : 'не выполнено') + '</span></div>' +
    '<div class="workflow-prompt">' + escapeHtml(item.summary || '') + '</div><div class="memory-meta"><span>' +
    escapeHtml(stamp) + '</span>' + (canUndo ? '<button type="button" class="btn small" data-action="undo-action" data-id="' + escapeAttr(item.id) + '">Отменить</button>' : '') +
    '</div></article>';
}

async function refreshActions() {
  try {
    const data = await api('/api/actions');
    state.actions = data.actions || [];
    if (state.drawerMode === 'actions') renderActionsDrawer();
  } catch (err) {
    if (state.drawerMode === 'actions') dom.drawerBody.innerHTML =
      '<div class="empty-state">Не удалось загрузить журнал: ' + escapeHtml(err.message) + '</div>';
  }
}

function renderActionsDrawer() {
  state.drawerMode = 'actions';
  dom.drawerTitle.textContent = 'История действий';
  const cards = state.actions.length
    ? state.actions.map(actionCard).join('')
    : '<div class="empty-state"><b>Пока тихо</b>Здесь появятся изменения, выполненные инструментами Кванты.</div>';
  dom.drawerBody.innerHTML =
    '<div class="drawer-actions"><span class="form-note">Содержимое команд, буфера и заметок в журнал не записывается.</span>' +
    '<button type="button" class="btn small" data-action="clear-actions">Очистить</button></div>' +
    '<div class="action-list">' + cards + '</div>';
}

async function renderDiagnosticsDrawer() {
  state.drawerMode = 'diagnostics';
  dom.drawerTitle.textContent = 'Состояние системы';
  dom.drawerBody.innerHTML = '<div class="empty-state">Проверяю Ollama, голос и ресурсы…</div>';
  try {
    const results = await Promise.all([api('/api/health'), api('/api/voice/status')]);
    if (state.drawerMode !== 'diagnostics') return;
    const health = results[0].health || {};
    const sys = results[0].system || {};
    const voiceInfo = results[1].voice || {};
    const hotkey = health.hotkey || {};
    const warmup = health.model_warmup || {};
    const resource = health.resource_profile || {};
    const voicePerf = state.lastVoiceMetrics;
    const whisper = voiceInfo.whisper || {};
    const ollamaOk = health.provider === 'ollama' ? health.ollama : health.ok;
    const loaded = (health.loaded_models || []).map((m) => escapeHtml(m.name || '')).filter(Boolean);
    const freeRam = Number(sys.ram_free ?? Math.max(0, Number(sys.ram_total || 0) - Number(sys.ram_used || 0)));
    const freeDisk = Number(sys.disk_free ?? Math.max(0, Number(sys.disk_total || 0) - Number(sys.disk_used || 0)));
    const qwen = voiceInfo.qwen_talker || {};
    const badges =
      '<div class="diagnostic-grid">' +
        '<div class="diagnostic-card"><small>Чат · ' + escapeHtml(health.provider || '—') + '</small><b class="' + (ollamaOk ? 'ok' : 'bad') + '">' + (ollamaOk ? 'Подключён' : 'Нет связи') + '</b><span>' + escapeHtml(health.model || '') + '</span></div>' +
        '<div class="diagnostic-card"><small>Профиль ресурсов</small><b class="' + (resource.status === 'error' ? 'warn' : 'ok') + '">' + (resource.profile === 'game' ? 'Игра' : 'Работа') + (resource.status === 'switching' ? ' · смена' : '') + '</b><span>' + escapeHtml(resource.message || 'Готов') + '</span></div>' +
        '<div class="diagnostic-card"><small>Модель чата</small><b>' + (loaded.length ? 'В памяти' : warmup.status === 'loading' ? 'Прогревается' : warmup.status === 'error' ? 'Ошибка прогрева' : warmup.status === 'disabled' || warmup.status === 'skipped' ? 'По запросу' : 'Проверяю') + '</b><span>' + (loaded.length ? loaded.join(', ') : escapeHtml(warmup.message || 'Кэш Ollama')) + '</span></div>' +
        '<div class="diagnostic-card"><small>Голос · ' + escapeHtml(voiceInfo.engine || '—') + '</small><b class="' + (qwen.ready ? 'ok' : 'bad') + '">' + (qwen.ready ? (qwen.loaded ? 'Загружен' : 'Готов') : 'Недоступен') + '</b><span>' + escapeHtml(qwen.backend || qwen.model || '') + '</span></div>' +
        '<div class="diagnostic-card"><small>Последний TTS-фрагмент</small><b class="' + (voicePerf && Number(voicePerf.rtf) > 1 ? 'warn' : 'ok') + '">' + (voicePerf ? `${Number(voicePerf.request_s).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} с · RTF ${voicePerf.rtf == null ? '—' : Number(voicePerf.rtf).toLocaleString('ru-RU', { maximumFractionDigits: 2 }) + '×'}` : 'Нет замера') + '</b><span>' + (voicePerf ? `Генерация ${Number(voicePerf.generation_s).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} с · аудио ${Number(voicePerf.audio_s).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} с · ${escapeHtml(voicePerf.backend || 'движок')}` : 'Появится после голосового ответа') + '</span></div>' +
        '<div class="diagnostic-card"><small>Распознавание · Whisper</small><b>' + (whisper.loaded ? escapeHtml(String(whisper.device || 'CPU').toUpperCase()) : escapeHtml(String(whisper.device || 'AUTO').toUpperCase())) + '</b><span>' + escapeHtml(whisper.error || (whisper.ready ? whisper.model_size || 'Готово' : 'Недоступно')) + '</span></div>' +
        '<div class="diagnostic-card"><small>ОЗУ свободно</small><b class="' + (freeRam < 3 ? 'warn' : 'ok') + '">' + freeRam.toFixed(1) + ' ГБ</b><span>' + Number(sys.ram_percent || 0).toFixed(0) + '% занято</span></div>' +
        '<div class="diagnostic-card"><small>Диск свободен</small><b class="' + (freeDisk < 25 ? 'warn' : 'ok') + '">' + freeDisk.toFixed(1) + ' ГБ</b><span>' + Number(sys.disk_percent || 0).toFixed(0) + '% занято</span></div>' +
        '<div class="diagnostic-card"><small>Быстрый вызов</small><b class="' + (hotkey.registered ? 'ok' : 'warn') + '">' + (hotkey.registered ? 'Работает' : 'Выключен') + '</b><span>' + escapeHtml(hotkey.shortcut || 'Ctrl+Alt+Space') + '</span></div>' +
      '</div>' +
      '<div class="settings-section"><h4>Подсказка</h4><div class="form-note">' +
      (freeRam < 3 ? 'Свободной RAM меньше 3 ГБ. Можно отключить прогрев модели при запуске или закрыть тяжёлые приложения.' : warmup.status === 'loading' ? 'Модель загружается в фоне; после прогрева первый ответ будет быстрее.' : warmup.status === 'error' ? 'Фоновый прогрев не удался. Первый запрос попробует загрузить модель ещё раз.' : warmup.status === 'ready' || loaded.length ? 'Модель уже в памяти и останется там в течение выбранного времени.' : 'При запуске Кванта прогреет модель, если включён этот параметр.') +
      '</div></div>' +
      '<button type="button" class="btn small" data-action="refresh-diagnostics">Обновить проверки</button>';
    dom.drawerBody.innerHTML = badges;
  } catch (err) {
    if (state.drawerMode === 'diagnostics') dom.drawerBody.innerHTML =
      '<div class="empty-state">Проверка не завершилась: ' + escapeHtml(err.message) + '</div>';
  }
}

async function forgetMemory(id) {
  const item = state.memories.find((entry) => entry.id === id);
  const ok = await confirmDialog({ title: 'Забыть запись?', text: item?.text || 'Запись будет удалена из памяти.', confirmLabel: 'Забыть', danger: true });
  if (!ok) return;
  try {
    await api('/api/memory/' + encodeURIComponent(id), { method: 'DELETE' });
    await refreshMemory();
    toast('Запись забыта', 'В новых ответах она больше не появится.', 'success', 2400);
  } catch (err) { toast('Не удалось удалить запись', err.message, 'error'); }
}

async function saveMemory(text) {
  try {
    await api('/api/memory', { method: 'POST', body: JSON.stringify({ text }) });
    await refreshMemory();
    toast('Запомнила', 'Запись будет доступна в следующих ответах.', 'success', 2200);
  } catch (err) { toast('Не удалось сохранить память', err.message, 'error'); }
}

async function deleteWorkflow(id) {
  const item = state.workflows.find((entry) => entry.id === id);
  const ok = await confirmDialog({ title: 'Удалить сценарий?', text: item?.title || 'Сценарий будет удалён.', confirmLabel: 'Удалить', danger: true });
  if (!ok) return;
  try {
    await api('/api/workflows/' + encodeURIComponent(id), { method: 'DELETE' });
    await refreshWorkflows();
    toast('Сценарий удалён', '', 'success', 1800);
  } catch (err) { toast('Не удалось удалить сценарий', err.message, 'error'); }
}

function runWorkflow(id) {
  const item = state.workflows.find((entry) => entry.id === id);
  if (!item) return;
  closeDrawer();
  handlePreset(item.prompt || '');
}

async function undoAction(id) {
  const item = state.actions.find((entry) => entry.id === id);
  const undoText = {
    remove_note: 'Созданная заметка будет удалена.',
    remove_reminder: 'Созданное напоминание будет удалено.',
    remove_memory: 'Добавленная запись памяти будет удалена.',
    remove_file: 'Новый файл отправится в Корзину, если после создания он не менялся.',
    move_file: 'Файл переместится обратно, если исходный путь свободен и файл не менялся.',
  }[item?.undo?.kind] || 'Приложение проверит, можно ли безопасно отменить изменение.';
  const ok = await confirmDialog({
    title: 'Отменить действие?',
    text: (item?.label || 'Действие') + ' · ' + (item?.summary || '') + '. ' + undoText,
    confirmLabel: 'Отменить',
  });
  if (!ok) return;
  try {
    const data = await api('/api/actions/' + encodeURIComponent(id) + '/undo', { method: 'POST', body: '{}' });
    if (!data.ok) throw new Error(data.text || 'Отмена недоступна');
    await refreshActions();
    toast('Действие отменено', data.text || '', 'success', 2200);
  } catch (err) { toast('Не удалось отменить', err.message, 'error'); }
}

async function clearActionHistory() {
  const ok = await confirmDialog({ title: 'Очистить историю действий?', text: 'Сами файлы и настройки не изменятся. Удалится только локальный журнал.', confirmLabel: 'Очистить', danger: true });
  if (!ok) return;
  try {
    await api('/api/actions/clear', { method: 'POST', body: '{}' });
    await refreshActions();
    toast('Журнал очищен', '', 'success', 1800);
  } catch (err) { toast('Не удалось очистить журнал', err.message, 'error'); }
}

function renderAnalyticsDrawer() {
  state.drawerMode = 'analytics';
  dom.drawerTitle.textContent = 'Аналитика';
  const sys = state.system || {};
  const cpu = Number(sys.cpu || 0);
  const ram = Number(sys.ram_percent || 0);
  const disk = Number(sys.disk_percent || 0);
  dom.drawerBody.innerHTML = `
    <div class="settings-section">
      <h4>Живая телеметрия</h4>
      <div class="meter-row"><span>CPU</span><div class="meter"><i style="width:${clamp(cpu, 0, 100)}%"></i></div><b>${cpu.toFixed(0)}%</b></div>
      <div class="meter-row"><span>RAM</span><div class="meter"><i style="width:${clamp(ram, 0, 100)}%"></i></div><b>${ram.toFixed(0)}%</b></div>
      <div class="meter-row"><span>DISK</span><div class="meter"><i style="width:${clamp(disk, 0, 100)}%"></i></div><b>${disk.toFixed(0)}%</b></div>
      <div class="mini-stats" style="margin-top:14px">
        <div><span>${sys.ram_used ?? '—'} / ${sys.ram_total ?? '—'}</span><small>ГБ ОЗУ</small></div>
        <div><span>${formatUptime(sys.uptime)}</span><small>uptime</small></div>
      </div>
    </div>
    <div class="settings-section">
      <h4>Активное окно</h4>
      <div class="active-app">${escapeHtml(sys.active_app || '—')}</div>
      <div class="active-title">${escapeHtml(sys.active_title || '')}</div>
    </div>
    <div class="settings-section">
      <h4>Что дальше</h4>
      <div class="form-note">
        Историческая аналитика — этап 3. Кванта будет локально записывать активные приложения,
        заголовки окон и время, строить паттерны и предлагать рутины. Запись можно будет
        поставить на паузу, а историю — удалить. Скрытого кейлоггера и сбора паролей нет.
      </div>
    </div>
    <button type="button" class="btn small" data-action="open-settings">Открыть настройки</button>`;
}

async function refreshNotes() {
  try {
    const data = await api('/api/notes');
    state.notes = data.notes || [];
    if (state.drawerMode === 'notes') renderNotesDrawer();
  } catch { /* ignore */ }
}

async function refreshReminders() {
  try {
    const data = await api('/api/reminders');
    state.reminders = data.reminders || [];
    const active = document.activeElement;
    const typing = active && dom.drawerBody?.contains(active) && ['INPUT', 'TEXTAREA'].includes(active.tagName);
    if (state.drawerMode === 'reminders' && !typing) renderRemindersDrawer();
  } catch { /* ignore */ }
}

async function createReminder(text, when) {
  try {
    await api('/api/reminders', {
      method: 'POST',
      body: JSON.stringify({ text, when }),
    });
    await refreshReminders();
    renderRemindersDrawer();
    toast('Напоминание создано', formatDateTime(when), 'success');
  } catch (err) {
    toast('Не удалось создать напоминание', err.message, 'error');
  }
}

async function deleteReminder(id) {
  try {
    await api(`/api/reminders/${encodeURIComponent(id)}`, { method: 'DELETE' });
    await refreshReminders();
    renderRemindersDrawer();
  } catch (err) {
    toast('Не удалось удалить напоминание', err.message, 'error');
  }
}

async function markReminderDone(id) {
  try {
    await api(`/api/reminders/${encodeURIComponent(id)}/done`, {
      method: 'POST',
      body: JSON.stringify({ done: true }),
    });
    await refreshReminders();
    renderRemindersDrawer();
  } catch (err) {
    toast('Не удалось обновить напоминание', err.message, 'error');
  }
}

/* ------------------------------------------------------------------ notes editor */
function openNoteEditor(note = null) {
  const isEdit = !!note;
  const modal = openModal(`
    <form id="noteForm">
      <div class="modal-head">
        <h3>${isEdit ? 'Редактировать заметку' : 'Новая заметка'}</h3>
        <button type="button" class="icon-btn" data-close>×</button>
      </div>
      <div class="modal-body">
        <div class="field"><label>Заголовок</label><input id="noteTitle" maxlength="200" value="${escapeAttr(note?.title || '')}" placeholder="О чём заметка?" required></div>
        <div class="field" style="margin-top:10px"><label>Текст</label><textarea id="noteText" placeholder="Текст заметки…">${escapeHtml(note?.text || '')}</textarea></div>
      </div>
      <div class="modal-foot">
        <button type="button" class="btn" data-close>Отмена</button>
        <button type="submit" class="btn primary">Сохранить</button>
      </div>
    </form>
  `, { dismissible: false });
  const form = modal.el.querySelector('#noteForm');
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const title = modal.el.querySelector('#noteTitle').value.trim();
    const text = modal.el.querySelector('#noteText').value;
    if (!title) return;
    try {
      if (isEdit) {
        await api(`/api/notes/${encodeURIComponent(note.id)}`, {
          method: 'PUT',
          body: JSON.stringify({ title, text }),
        });
      } else {
        await api('/api/notes', {
          method: 'POST',
          body: JSON.stringify({ title, text }),
        });
      }
      modal.close();
      await refreshNotes();
      if (state.drawerMode === 'notes') renderNotesDrawer();
      toast(isEdit ? 'Заметка обновлена' : 'Заметка создана', title, 'success', 2400);
    } catch (err) {
      toast('Заметка не сохранена', err.message, 'error');
    }
  });
  setTimeout(() => modal.el.querySelector('#noteTitle')?.focus(), 30);
}

async function deleteNote(id) {
  const note = state.notes.find((n) => n.id === id);
  const ok = await confirmDialog({
    title: 'Удалить заметку?',
    text: `«${note?.title || 'Заметка'}» будет удалена без возможности отмены.`,
    confirmLabel: 'Удалить',
    danger: true,
  });
  if (!ok) return;
  try {
    await api(`/api/notes/${encodeURIComponent(id)}`, { method: 'DELETE' });
    await refreshNotes();
    if (state.drawerMode === 'notes') renderNotesDrawer();
    toast('Заметка удалена', '', 'success', 2000);
  } catch (err) {
    toast('Не удалось удалить заметку', err.message, 'error');
  }
}

/* ------------------------------------------------------------------ settings */
function openSettings() {
  if (!state.settings) return;
  const s = state.settings;
  const modelOptions = state.models
    .map((m) => (typeof m === 'string' ? m : m.name))
    .filter(Boolean)
    .map((name) => `<option value="${escapeAttr(name)}"></option>`)
    .join('');
  const opt = (value, label, selected) => `<option value="${escapeAttr(value)}"${value === selected ? ' selected' : ''}>${escapeHtml(label)}</option>`;
  const checked = (value) => (value ? 'checked' : '');
  const modal = openModal(`
    <form id="settingsForm">
      <div class="modal-head">
        <div><h3>Настройки Q.W.A.N.T.A.</h3><p>Локально. Данные не уходят в облако без твоего выбора.</p></div>
        <button type="button" class="icon-btn" data-close>×</button>
      </div>
      <div class="modal-body">
        <div class="settings-section">
          <h4>Нейроядро</h4>
          <div class="settings-grid">
            <div class="field"><label>Провайдер</label><select id="setProvider">${opt('ollama', 'Ollama (локально)', s.provider)}${opt('openai', 'OpenAI-совместимый API', s.provider)}</select></div>
            <div class="field"><label>Модель</label><input id="setModel" list="modelOptions" value="${escapeAttr(s.model)}" placeholder="qwen3:4b-instruct"><datalist id="modelOptions">${modelOptions}</datalist></div>
          </div>
          <div class="field" style="margin-top:10px"><label>Адрес Ollama</label><input id="setOllama" value="${escapeAttr(s.ollama_url)}"></div>
          <div class="settings-grid" style="margin-top:10px">
            <div class="field"><label>API URL</label><input id="setApiUrl" value="${escapeAttr(s.api_url)}"></div>
            <div class="field"><label>API-ключ</label><input id="setApiKey" type="password" value="${escapeAttr(s.api_key)}" autocomplete="off"></div>
          </div>
          <div class="settings-grid" style="margin-top:10px">
            <div class="field"><label>Температура</label><input id="setTemperature" type="number" min="0" max="2" step="0.05" value="${escapeAttr(s.temperature)}"></div>
            <div class="field"><label>Максимум шагов</label><input id="setMaxSteps" type="number" min="1" max="20" value="${escapeAttr(s.max_steps)}"></div>
          </div>
          <div class="settings-grid" style="margin-top:10px">
            <div class="field"><label>Контекст модели</label><select id="setModelContext">${opt('8192', '8K · экономно', String(s.model_context_size))}${opt('12288', '12K · рекомендую', String(s.model_context_size))}${opt('16384', '16K · длинные чаты', String(s.model_context_size))}</select></div>
            <div class="field"><label>Держать модель в памяти</label><select id="setModelKeepAlive">${opt('2m', '2 минуты · меньше RAM', s.model_keep_alive)}${opt('5m', '5 минут · баланс', s.model_keep_alive)}${opt('10m', '10 минут · быстрее повторно', s.model_keep_alive)}</select></div>
          </div>
          <div class="form-note" style="margin-top:8px">На GTX 1070 8K–12K быстрее освобождают видеопамять. Длинное удержание ускоряет повторный ответ, но занимает память.</div>
          <div class="switch-row" style="margin-top:10px"><span>Прогревать модель Ollama при запуске (быстрее первый ответ, занимает память)</span><label class="switch"><input type="checkbox" id="setModelWarmup" ${checked(s.model_warmup_on_start)}><i></i></label></div>
        </div>

        <div class="settings-section">
          <h4>Характер</h4>
          <div class="settings-grid">
            <div class="field"><label>Обращение (необязательно)</label><input id="setAddress" placeholder="Не использовать имя" value="${escapeAttr(s.address)}"></div>
            <div class="field"><label>Сарказм (1–5)</label><input id="setSarcasm" type="number" min="1" max="5" value="${escapeAttr(s.sarcasm)}"></div>
          </div>
          <div class="field" style="margin-top:10px"><label>Системная личность</label><textarea id="setPrompt" style="min-height:120px">${escapeHtml(s.system_prompt)}</textarea></div>
          <div class="switch-row"><span>Проактивные предложения</span><label class="switch"><input type="checkbox" id="setProactive" ${checked(s.proactive)}><i></i></label></div>
          <div class="switch-row"><span>Подмешивать личную память в новые ответы</span><label class="switch"><input type="checkbox" id="setMemoryEnabled" ${checked(s.memory_enabled)}><i></i></label></div>
          <div class="switch-row"><span>Глобальный вызов Ctrl+Alt+Space</span><label class="switch"><input type="checkbox" id="setGlobalHotkey" ${checked(s.global_hotkey_enabled)}><i></i></label></div>
        </div>

        <div class="settings-section">
          <h4>Доступ и файлы</h4>
          <div class="settings-grid">
            <div class="field"><label>Режим доступа</label><select id="setAccess">${opt('safe', 'Безопасный', s.access_mode)}${opt('smart', 'Умный', s.access_mode)}${opt('auto', 'Авто', s.access_mode)}</select></div>
            <div class="field"><label>Режим интерфейса</label><select id="setUiMode">${opt('mini', 'Компактный режим', s.ui_mode)}${opt('standard', 'Рабочее окно', s.ui_mode)}${opt('hud', 'Полный экран', s.ui_mode)}</select></div>
          </div>
          <div class="field" style="margin-top:10px"><label>Рабочая папка</label><input id="setWorkDir" value="${escapeAttr(s.work_dir)}"></div>
          <div class="field" style="margin-top:10px"><label>Город для погоды</label><input id="setWeatherCity" value="${escapeAttr(s.weather_city)}"></div>
          <div class="form-note" style="margin-top:8px">Безопасный — только чтение и безопасные действия. Умный — опасное с подтверждением. Авто — без подтверждений; включай осознанно.</div>
        </div>

        <div class="settings-section">
          <h4>Данные и чувствительный доступ</h4>
          <div class="switch-row"><span>Локальная аналитика</span><label class="switch"><input type="checkbox" id="setAnalytics" ${checked(s.analytics)}><i></i></label></div>
          <div class="switch-row"><span>Заголовки активных окон</span><label class="switch"><input type="checkbox" id="setTitles" ${checked(s.analytics_window_titles)}><i></i></label></div>
          <div class="settings-grid" style="margin-top:8px">
            <div class="field"><label>Хранить историю, дней</label><input id="setRetention" type="number" min="1" max="365" value="${escapeAttr(s.analytics_retention_days)}"></div>
            <div class="field"><label>Чувствительный доступ</label><select id="setSensitive">${opt('ask', 'Спрашивать разрешение', s.sensitive_access)}${opt('off', 'Выключен', s.sensitive_access)}</select></div>
          </div>
          <div class="form-note" style="margin-top:8px">
            Историческая аналитика появится на этапе 3. Скрытого кейлоггера нет. Запись ввода и работа с паролями
            будут отдельным протоколом с явным разрешением, индикатором REC и ограничением по приложению.
            Пароли не сохраняются в чате или заметках.
          </div>
        </div>

        <div class="settings-section">
          <h4>Голос</h4>
          <div class="settings-grid">
            <div class="switch-row"><span>Голосовой синтез включён</span><label class="switch"><input type="checkbox" id="setVoiceEnabled" ${checked(s.voice_enabled)}><i></i></label></div>
            <div class="field" style="margin-top:8px"><label>Движок TTS</label><select id="setVoiceEngine">${opt('qwen_talker', 'Qwen-Talker 1.7B VoiceDesign Q8 — голос по промпту', s.voice_engine)}${opt('silero', 'Silero v4 — быстрый русский', s.voice_engine)}${opt('qwen', 'Qwen3-TTS CustomVoice — Serena и другие', s.voice_engine)}${opt('voxcpm', 'VoxCPM2 — локальный голос', s.voice_engine)}${opt('sapi', 'Windows SAPI', s.voice_engine)}</select></div>
            <div class="field" style="margin-top:8px"><label>Размер модели голоса</label><select id="setTtsModel">${opt('1.7b', '1.7B VoiceDesign · Q8_0', '1.7b')}</select></div>
            <div class="field" style="margin-top:8px"><label>Профиль</label><select id="setVoiceName">${[['Quanta', 'Quanta — VoiceDesign по промпту'], ['baya', 'Baya — женский (Silero)'], ['kseniya', 'Kseniya — женский (Silero)'], ['xenia', 'Xenia — женский (Silero)'], ['Serena', 'Serena — женский (CustomVoice)'], ['Vivian', 'Vivian — женский (CustomVoice)'], ['Ono_Anna', 'Ono_Anna — женский (CustomVoice)'], ['Sohee', 'Sohee — женский (CustomVoice)']].map(([value, label]) => opt(value, label, s.voice_name || 'Quanta')).join('')}</select></div>
            <div class="field" style="margin-top:8px"><label>Описание голоса (Qwen VoiceDesign / Qwen / VoxCPM2)</label><input id="setVoiceInstruct" value="${escapeAttr(s.voice_instruct)}"></div>
            <div class="field" style="margin-top:8px"><label>Скорость речи</label><select id="setTtsSpeed">${opt('0.95', '0.95× — спокойно', String(s.tts_speed ?? 1.0))}${opt('1.0', '1.00× — естественно', String(s.tts_speed ?? 1.0))}${opt('1.05', '1.05× — размеренно', String(s.tts_speed ?? 1.0))}${opt('1.1', '1.10× — бодро', String(s.tts_speed ?? 1.0))}${opt('1.2', '1.20× — быстро', String(s.tts_speed ?? 1.0))}</select></div>
            <div class="field" style="margin-top:8px"><label>Устройство TTS</label><select id="setVoiceDevice">${opt('cpu', 'CPU — надёжно для этой GTX 1070', s.voice_device)}${opt('auto', 'Авто (GPU/CPU)', s.voice_device)}${opt('cuda', 'GPU CUDA', s.voice_device)}</select></div>
            <div class="field" style="margin-top:8px"><label>ASR модель</label><select id="setSttModel">${opt('tiny', 'faster-whisper tiny', s.stt_model)}${opt('base', 'faster-whisper base', s.stt_model)}${opt('small', 'faster-whisper small', s.stt_model)}${opt('medium', 'faster-whisper medium', s.stt_model)}</select></div>
            <div class="field" style="margin-top:8px"><label>Распознавание речи</label><select id="setSttDevice">${opt('auto', 'Авто · GPU, если помещается; иначе CPU', s.stt_device || 'auto')}${opt('cpu', 'CPU · экономно по видеопамяти', s.stt_device || 'auto')}${opt('cuda', 'GPU CUDA · обязательно', s.stt_device || 'auto')}</select></div>
          </div>
          <div class="settings-grid" style="margin-top:10px">
            <div class="switch-row"><span>Числа, даты и время произносить словами</span><label class="switch"><input type="checkbox" id="setSpeechNumbers" ${checked(s.speech_normalize_numbers)}><i></i></label></div>
            <div class="switch-row"><span>Автоударения RUAccent (не применяются к Qwen-Talker)</span><label class="switch"><input type="checkbox" id="setSpeechStress" ${checked(s.speech_auto_stress)}><i></i></label></div>
            <div class="switch-row"><span>Очистка артефактов синтеза Qwen-Talker</span><label class="switch"><input type="checkbox" id="setSpeechNoiseReduction" ${checked(s.speech_noise_reduction ?? true)}><i></i></label></div>
            <div class="switch-row"><span>Шумоподавление микрофона</span><label class="switch"><input type="checkbox" id="setNoiseSuppression" ${checked(s.noise_suppression)}><i></i></label></div>
            <div class="switch-row"><span>Подавление эха микрофона</span><label class="switch"><input type="checkbox" id="setEchoCancellation" ${checked(s.echo_cancellation)}><i></i></label></div>
            <div class="switch-row"><span>Автоуровень микрофона</span><label class="switch"><input type="checkbox" id="setAutoGain" ${checked(s.auto_gain_control)}><i></i></label></div>
          </div>
          <div class="form-note" style="margin-top:8px">Числа раскрываются с учётом падежа. RUAccent помечает ударения знаком «+» внутри слова; Qwen-Talker получает обычную русскую орфографию, потому что эти метки искажают звучание. Очистка Qwen-Talker мягко подавляет фоновые артефакты через FFmpeg; микрофон отдельно обрабатывает браузер и драйвер Windows.</div>
          <div class="form-note" style="margin-top:10px">Qwen-Talker VoiceDesign использует локальную модель 1.7B Q8_0 и точный промпт из файла «промт для голоса.txt». Первый синтез загружает движок и веса; после прогрева модель остаётся в памяти для следующих реплик. Если Q8-модель или Qwen-TTS Studio не найдены, Quanta покажет ошибку здесь.</div>
          <div class="switch-row" style="margin-top:10px"><span>Автоматически отправлять распознанное</span><label class="switch"><input type="checkbox" id="setVoiceAutoSend" ${checked(s.voice_auto_send)}><i></i></label></div>
          <div class="switch-row" style="margin-top:8px"><span>Wake word «Кванта» (следующий этап)</span><label class="switch"><input type="checkbox" id="setWakeWord" ${checked(s.wake_word)}><i></i></label></div>
          <div class="form-note" style="margin-top:10px" id="voiceStatusNote">Проверка возможностей…</div>
          <div class="form-note" style="margin-top:6px" id="voiceProcessingStatusNote">Проверка RUAccent…</div>
          <div class="form-note" style="margin-top:6px" id="voiceInputStatusNote">Параметры шумоподавления проверятся при первой записи.</div>
        </div>

        <div class="settings-section">
          <h4>Система</h4>
          <div class="settings-grid">
            <div class="field"><label>Порт сервера</label><input value="127.0.0.1:8765" disabled></div>
            <div class="field"><label>Версия</label><input value="${escapeAttr(state.version)}" disabled></div>
          </div>
          <div class="drawer-actions" style="margin-top:12px">
            <button type="button" class="btn small" id="settingsCheck">Проверить связь</button>
            <button type="button" class="btn small" id="settingsData">Открыть папку данных</button>
          </div>
          <button type="button" class="btn danger small" id="settingsShutdown" style="margin-top:10px">Завершить Q.W.A.N.T.A.</button>
        </div>
      </div>
      <div class="modal-foot">
        <button type="button" class="btn" data-close>Отмена</button>
        <button type="submit" class="btn primary">Сохранить</button>
      </div>
    </form>
  `, { wide: true, dismissible: false });

  loadVoiceStatus().then((voiceStatus) => {
    const note = modal.el.querySelector('#voiceStatusNote');
    if (!note) return;
    const native = voiceStatus?.qwen_talker;
    if (native?.loaded) {
      const lowMemory = Number(native.available_ram_gb) < 2
        ? ` Сейчас свободно ${native.available_ram_gb} ГБ; RUAccent освободит около 0,7 ГБ после трёх минут без речи.`
        : '';
      note.textContent = `Qwen-Talker загружена (${native.backend || 'native'}); модель ${native.model || 'VoiceDesign Q8_0'}.${lowMemory}`;
    } else if (native?.resource_warning) {
      note.textContent = native.resource_warning;
    } else if (native?.ready) {
      note.textContent = `Qwen-Talker VoiceDesign ${native.model || 'Q8_0'} найдена. Загрузится при первом синтезе${native.error ? `; последняя ошибка: ${native.error}` : ''}.`;
    } else {
      note.textContent = `Qwen-Talker не готова: проверь Qwen-TTS Studio и локальные GGUF-модели${native?.error ? ` (${native.error})` : ''}.`;
    }
    const processingNote = modal.el.querySelector('#voiceProcessingStatusNote');
    const stress = voiceStatus?.speech_processing;
    if (processingNote) {
      processingNote.textContent = stress?.loaded
        ? 'RUAccent загружен; автоударения готовы.'
        : stress?.ready
          ? 'RUAccent установлен; модель ударений загрузится при первой русской реплике.'
          : 'RUAccent недоступен; установи зависимости из requirements-voice.txt для автоударений.';
    }
  });
  updateVoiceInputStatus(modal.el);

  const form = modal.el.querySelector('#settingsForm');
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const patch = {
      provider: form.querySelector('#setProvider').value,
      model: form.querySelector('#setModel').value.trim(),
      ollama_url: form.querySelector('#setOllama').value.trim(),
      api_url: form.querySelector('#setApiUrl').value.trim(),
      api_key: form.querySelector('#setApiKey').value,
      temperature: Number(form.querySelector('#setTemperature').value),
      max_steps: Number(form.querySelector('#setMaxSteps').value),
      model_context_size: Number(form.querySelector('#setModelContext').value),
      model_keep_alive: form.querySelector('#setModelKeepAlive').value,
      model_warmup_on_start: form.querySelector('#setModelWarmup').checked,
      address: form.querySelector('#setAddress').value.trim(),
      sarcasm: Number(form.querySelector('#setSarcasm').value),
      system_prompt: form.querySelector('#setPrompt').value,
      proactive: form.querySelector('#setProactive').checked,
      memory_enabled: form.querySelector('#setMemoryEnabled').checked,
      global_hotkey_enabled: form.querySelector('#setGlobalHotkey').checked,
      access_mode: form.querySelector('#setAccess').value,
      ui_mode: form.querySelector('#setUiMode').value,
      work_dir: form.querySelector('#setWorkDir').value.trim(),
      weather_city: form.querySelector('#setWeatherCity').value.trim(),
      analytics: form.querySelector('#setAnalytics').checked,
      analytics_window_titles: form.querySelector('#setTitles').checked,
      analytics_retention_days: Number(form.querySelector('#setRetention').value),
      sensitive_access: form.querySelector('#setSensitive').value,
      voice_enabled: form.querySelector('#setVoiceEnabled').checked,
      voice_engine: form.querySelector('#setVoiceEngine').value,
      tts_model: form.querySelector('#setTtsModel').value,
      voice_name: form.querySelector('#setVoiceName').value.trim(),
      voice_instruct: form.querySelector('#setVoiceInstruct').value.trim(),
      tts_speed: Number(form.querySelector('#setTtsSpeed').value),
      voice_device: form.querySelector('#setVoiceDevice').value,
      speech_normalize_numbers: form.querySelector('#setSpeechNumbers').checked,
      speech_auto_stress: form.querySelector('#setSpeechStress').checked,
      speech_noise_reduction: form.querySelector('#setSpeechNoiseReduction').checked,
      noise_suppression: form.querySelector('#setNoiseSuppression').checked,
      echo_cancellation: form.querySelector('#setEchoCancellation').checked,
      auto_gain_control: form.querySelector('#setAutoGain').checked,
      stt_model: form.querySelector('#setSttModel').value,
      stt_device: form.querySelector('#setSttDevice').value,
      voice_auto_send: form.querySelector('#setVoiceAutoSend').checked,
      wake_word: form.querySelector('#setWakeWord').checked,
    };
    try {
      const saved = await saveSettings(patch, { silent: true });
      const targetMode = saved.ui_mode;
      modal.close();
      if (targetMode !== state.mode) setMode(targetMode, { persist: false });
      toast('Настройки сохранены', 'Ядро обновлено.', 'success', 2400);
      loadModels(false);
      loadVoiceStatus().catch(() => {});
    } catch (err) {
      toast('Настройки не сохранены', err.message, 'error', 7000);
    }
  });

  modal.el.querySelector('#settingsCheck')?.addEventListener('click', async () => {
    try {
      const data = await api('/api/models');
      state.models = data.models || [];
      renderModelSelect();
      toast('Связь есть', `Доступно моделей: ${state.models.length}`, 'success');
    } catch (err) {
      toast('Ошибка связи', err.message, 'error', 7000);
    }
  });

  modal.el.querySelector('#settingsData')?.addEventListener('click', async () => {
    try {
      await api('/api/open', { method: 'POST', body: JSON.stringify({ path: state.dataDir }) });
    } catch (err) {
      toast('Не удалось открыть папку', err.message, 'error');
    }
  });

  modal.el.querySelector('#settingsShutdown')?.addEventListener('click', async () => {
    const ok = await confirmDialog({
      title: 'Завершить Q.W.A.N.T.A.?',
      text: 'Сервер остановится. Несохранённые ответы прервутся. Данные останутся на диске.',
      confirmLabel: 'Завершить',
      danger: true,
    });
    if (!ok) return;
    try {
      await api('/api/shutdown', { method: 'POST', body: '{}' });
    } catch { /* server may close immediately */ }
    closeModals();
    toast('Ядро остановлено', 'Можешь закрыть окно.', 'warn', 6000);
    setTimeout(() => {
      try { window.close(); } catch { /* ignore */ }
    }, 600);
  });
}

function beep() {
  try {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === 'suspended') audioCtx.resume();
    const oscillator = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    oscillator.type = 'sine';
    oscillator.frequency.setValueAtTime(880, audioCtx.currentTime);
    oscillator.frequency.exponentialRampToValueAtTime(1320, audioCtx.currentTime + 0.12);
    gain.gain.setValueAtTime(0.0001, audioCtx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.12, audioCtx.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 0.35);
    oscillator.connect(gain);
    gain.connect(audioCtx.destination);
    oscillator.start();
    oscillator.stop(audioCtx.currentTime + 0.4);
  } catch { /* audio can be blocked until user gesture */ }
}

/* ------------------------------------------------------------------ system/weather */
function updateStatus(health) {
  if (!health || !dom.statusDot) return;
  const provider = health.provider || 'ollama';
  if (provider === 'ollama') {
    if (health.ollama) {
      dom.statusDot.className = 'dot online';
      dom.statusText.textContent = `Ollama${health.version ? ' · ' + health.version : ''}`;
    } else {
      dom.statusDot.className = 'dot offline';
      dom.statusText.textContent = 'Ollama не отвечает';
    }
  } else if (health.ok) {
    dom.statusDot.className = 'dot online';
    dom.statusText.textContent = `API · ${health.model || 'модель'}`;
  } else {
    dom.statusDot.className = 'dot offline';
    dom.statusText.textContent = 'API не отвечает';
  }
}

function applySystem(sys) {
  if (!sys) return;
  state.system = sys;
  const cpu = Number(sys.cpu || 0);
  const ram = Number(sys.ram_percent || 0);
  const disk = Number(sys.disk_percent || 0);
  if (dom.cpuBar) dom.cpuBar.style.width = `${clamp(cpu, 0, 100)}%`;
  if (dom.ramBar) dom.ramBar.style.width = `${clamp(ram, 0, 100)}%`;
  if (dom.diskBar) dom.diskBar.style.width = `${clamp(disk, 0, 100)}%`;
  if (dom.cpuVal) dom.cpuVal.textContent = `${Math.round(cpu)}%`;
  if (dom.ramVal) dom.ramVal.textContent = `${Math.round(ram)}%`;
  if (dom.diskVal) dom.diskVal.textContent = `${Math.round(disk)}%`;
  if (dom.batteryVal) {
    dom.batteryVal.textContent = sys.battery == null ? '—' : `${sys.battery}%${sys.plugged ? ' ⚡' : ''}`;
  }
  if (dom.uptimeVal) dom.uptimeVal.textContent = formatUptime(sys.uptime);
  if (dom.activeApp) dom.activeApp.textContent = sys.active_app || '—';
  if (dom.activeTitle) {
    dom.activeTitle.textContent = sys.active_title || '';
    dom.activeTitle.title = sys.active_title || '';
  }
  if (state.drawerMode === 'analytics') {
    const active = document.activeElement;
    if (!active || !dom.drawerBody?.contains(active)) renderAnalyticsDrawer();
  }
}

async function pollSystem() {
  try {
    const data = await api('/api/system');
    applySystem(data);
  } catch { /* ignore */ }
}

async function pollHealth() {
  try {
    const data = await api('/api/health');
    updateStatus(data.health);
    const resource = data.health?.resource_profile;
    if (resource && dom.resourceProfileSelect) {
      dom.resourceProfileSelect.value = resource.profile || state.settings?.resource_profile || 'work';
      dom.resourceProfileSelect.disabled = resource.status === 'switching';
      dom.resourceProfileSelect.title = resource.message || 'Профиль ресурсов';
    }
    if (data.system) applySystem(data.system);
    return resource || null;
  } catch {
    if (dom.statusDot) dom.statusDot.className = 'dot offline';
    if (dom.statusText) dom.statusText.textContent = 'Сервер недоступен';
    return null;
  }
}

async function watchResourceTransition() {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    const resource = await pollHealth();
    if (!dom.resourceProfileSelect?.disabled) return resource;
  }
  return false;
}

function weatherIcon(code) {
  code = Number(code);
  if (code === 0) return '☀';
  if (code >= 1 && code <= 3) return '⛅';
  if (code >= 45 && code <= 48) return '🌫';
  if (code >= 51 && code <= 67) return '🌧';
  if (code >= 71 && code <= 77) return '❄';
  if (code >= 80 && code <= 82) return '🌦';
  if (code >= 95) return '⛈';
  return '◌';
}

async function loadWeather() {
  try {
    const data = await api('/api/weather');
    if (dom.weatherCity) dom.weatherCity.textContent = data.city || state.settings?.weather_city || 'Краснодар';
    if (dom.weatherIcon) dom.weatherIcon.textContent = weatherIcon(data.code);
    if (dom.weatherTemp) dom.weatherTemp.textContent = data.temp == null ? '—' : `${Math.round(data.temp)}°C`;
    if (dom.weatherDesc) dom.weatherDesc.textContent = data.condition || '—';
    if (dom.weatherRange) {
      dom.weatherRange.textContent = (data.min != null && data.max != null)
        ? `от ${Math.round(data.min)}° до ${Math.round(data.max)}°`
        : '';
    }
  } catch {
    if (dom.weatherTemp) dom.weatherTemp.textContent = '—';
    if (dom.weatherDesc) dom.weatherDesc.textContent = 'погода недоступна';
  }
}

async function loadVoiceStatus() {
  try {
    const data = await api('/api/voice/status');
    state.voiceStatus = data.voice || null;
    return state.voiceStatus;
  } catch {
    state.voiceStatus = { enabled: false, engine: 'silero', tts_model: '1.7b', voice_name: 'baya', engine_ok: false };
    return state.voiceStatus;
  }
}

async function pollVoice() {
  await loadVoiceStatus();
}

function updateMicUI() {
  const mic = dom.micBtn;
  if (!mic) return;
  if (state.recording) {
    mic.classList.add('recording');
    mic.title = 'Отпускай — Кванта слушает';
  } else {
    mic.classList.remove('recording');
    mic.title = state.voiceStatus?.enabled ? 'Голосовой ввод' : 'Голосовой ввод (включён в настройках)';
  }
}

function updateVoiceInputStatus(root = document) {
  const note = root.querySelector?.('#voiceInputStatusNote');
  if (!note) return;
  const capture = state.voiceCaptureEffects;
  if (!capture) {
    note.textContent = 'Шумоподавление, эхоподавление и автоуровень будут запрошены у браузера; фактическое состояние покажется после записи.';
    return;
  }
  const names = {
    noiseSuppression: 'шумоподавление',
    echoCancellation: 'эхоподавление',
    autoGainControl: 'автоуровень',
  };
  const lines = Object.entries(names).map(([key, label]) => {
    const value = capture.actual[key];
    if (value === true) return `${label}: включено`;
    if (value === false) return `${label}: выключено`;
    if (capture.supported[key] === false) return `${label}: браузер не поддерживает`;
    return `${label}: браузер не сообщил`;
  });
  note.textContent = `Фактические эффекты микрофона: ${lines.join(' · ')}.`;
}

async function sendRecording(recorder) {
  if (!recorder) return;
  const chunks = [...recordingChunks];
  const mime = recorder.mimeType || 'audio/webm';
  await new Promise((resolve) => {
    const oldOnStop = recorder.onstop;
    const onStop = () => { if (oldOnStop) oldOnStop(); resolve(); };
    recorder.onstop = onStop;
    try {
      if (recorder.state === 'inactive') resolve();
      else recorder.stop();
    } catch { resolve(); }
  });
  if (mediaStream) {
    try { mediaStream.getTracks().forEach((track) => track.stop()); } catch { /* ignore */ }
  }
  const blob = new Blob(chunks, { type: mime });
  const buffer = await blob.arrayBuffer();
  let info = { ok: false, text: '', error: 'Запись недоступна' };
  try {
    const response = await fetch('/api/voice/transcribe', {
      method: 'POST',
      headers: { 'Content-Type': mime },
      body: buffer,
    });
    info = await response.json();
  } catch (err) { info = { ok: false, text: '', error: String(err) }; }
  state.recording = false;
  mediaRecorder = null;
  recordingChunks = [];
  updateMicUI();
  if (!info.ok) {
    toast('Распознавание не удалось', info.error || 'Проверь микрофон.', 'error', 5000);
    return;
  }
  const text = String(info.text || '').trim();
  if (!text) {
    toast('Кванта не поняла речь', 'Попробуй ещё раз.', 'warn', 4000);
    return;
  }
  if (state.settings?.voice_auto_send && !state.busy) {
    dom.input.value = text;
    autoGrowInput();
    sendMessage();
  } else {
    dom.input.value = text;
    autoGrowInput();
    dom.input.focus();
  }
}

async function startRecording() {
  if (state.recording) return;
  if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
    toast('Голосовой ввод недоступен', 'В этом браузере нет MediaRecorder / микрофона.', 'error', 6000);
    return;
  }
  try {
    const settings = state.settings || {};
    const requested = {
      echoCancellation: Boolean(settings.echo_cancellation ?? true),
      noiseSuppression: Boolean(settings.noise_suppression ?? true),
      autoGainControl: Boolean(settings.auto_gain_control ?? true),
    };
    const browserSupport = navigator.mediaDevices.getSupportedConstraints?.() || {};
    const audio = {};
    for (const [key, enabled] of Object.entries(requested)) {
      if (browserSupport[key] !== false) audio[key] = enabled;
    }
    const stream = await navigator.mediaDevices.getUserMedia({ audio });
    const track = stream.getAudioTracks?.()[0];
    state.voiceCaptureEffects = {
      requested,
      supported: Object.fromEntries(Object.keys(requested).map((key) => [key, browserSupport[key] !== false])),
      actual: track?.getSettings?.() || {},
    };
    updateVoiceInputStatus();
    const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].find((type) => MediaRecorder.isTypeSupported(type)) || '';
    const recorder = new MediaRecorder(stream, { mimeType: mime, audioBitsPerSecond: 128000 });
    recordingChunks = [];
    recorder.ondataavailable = (event) => {
      if (event.data && event.data.size) recordingChunks.push(event.data);
    };
    recorder.onstop = async () => {
      const r = recorder;
      mediaRecorder = null;
      try { stream.getTracks().forEach((track) => track.stop()); } catch { /* ignore */ }
      await sendRecording(r);
    };
    mediaRecorder = recorder;
    state.recording = true;
    updateMicUI();
    setOrbState('listening');
    recorder.start(250);
  } catch (err) {
    state.recording = false;
    updateMicUI();
    toast('Микрофон не разрешён', 'Разреши доступ к микрофону в Edge и попробуй снова.', 'error', 7000);
  }
}

async function stopRecording() {
  if (!mediaRecorder) return;
  const recorder = mediaRecorder;
  mediaRecorder = null;
  state.recording = false;
  updateMicUI();
  setOrbState('idle');
  try {
    if (recorder.state !== 'inactive') recorder.stop();
  } catch { /* ignore */ }
}

let speechRun = 0;
let currentSpeechAudio = null;
let streamingSpeech = null;

function resolveSpeechStreamIfFinished(stream) {
  if (!stream || stream.cancelled) return;
  if (stream.finalized && !stream.synthesizing && !stream.chunks.length && !stream.ready.length && !stream.playing) {
    const resolve = stream.resolveFinished;
    stream.resolveFinished = null;
    if (resolve) resolve();
    if (streamingSpeech === stream) {
      streamingSpeech = null;
      setOrbState('idle');
    }
  }
}

function stopSpeech() {
  speechRun += 1;
  if (streamingSpeech) {
    const stream = streamingSpeech;
    stream.cancelled = true;
    stream.chunks.length = 0;
    stream.ready.length = 0;
    const resolve = stream.resolveFinished;
    stream.resolveFinished = null;
    if (resolve) resolve();
    streamingSpeech = null;
  }
  if (currentSpeechAudio) {
    try { currentSpeechAudio.pause(); } catch { /* ignore */ }
    currentSpeechAudio = null;
  }
}

function beginStreamingSpeech() {
  if (!state.settings?.voice_enabled) return null;
  if (streamingSpeech && streamingSpeech.run === speechRun) return streamingSpeech;
  let resolveFinished;
  const finished = new Promise((resolve) => { resolveFinished = resolve; });
  streamingSpeech = {
    run: speechRun,
    sourceText: '',
    buffer: '',
    chunks: [],
    ready: [],
    synthesizing: false,
    playing: false,
    finalized: false,
    cancelled: false,
    warned: false,
    finished,
    resolveFinished,
  };
  return streamingSpeech;
}

function queueStreamingSpeechChunk(stream, text) {
  const chunk = String(text || '').trim();
  if (!chunk || stream.cancelled || stream.run !== speechRun) return;
  stream.chunks.push(chunk);
  pumpStreamingSynthesis(stream);
}

function drainStreamingSpeechBuffer(stream, final = false) {
  let rest = stream.buffer;
  while (rest) {
    const boundary = /[.!?…]+[»”"')\]]*\s+/.exec(rest);
    if (boundary) {
      const end = boundary.index + boundary[0].trimEnd().length;
      const chunk = rest.slice(0, end).trim();
      rest = rest.slice(boundary.index + boundary[0].length).trimStart();
      queueStreamingSpeechChunk(stream, chunk);
      continue;
    }
    if (rest.length > 180) {
      let split = rest.lastIndexOf(' ', 160);
      if (split < 80) split = rest.indexOf(' ', 160);
      if (split > 0) {
        queueStreamingSpeechChunk(stream, rest.slice(0, split));
        rest = rest.slice(split).trimStart();
        continue;
      }
    }
    break;
  }
  if (final && rest.trim()) {
    queueStreamingSpeechChunk(stream, rest);
    rest = '';
  }
  stream.buffer = rest;
}

function appendStreamingSpeechDelta(text) {
  const delta = String(text || '');
  if (!delta || !state.settings?.voice_enabled) return;
  const stream = beginStreamingSpeech();
  if (!stream || stream.cancelled) return;
  stream.sourceText += delta;
  stream.buffer += delta;
  drainStreamingSpeechBuffer(stream);
}

async function requestStreamingSpeechChunk(text) {
  const started = performance.now();
  try {
    const result = await api('/api/voice/tts', {
      method: 'POST',
      body: JSON.stringify({ text }),
    });
    if (result?.ok) {
      state.lastVoiceMetrics = {
        request_s: (performance.now() - started) / 1000,
        generation_s: Number(result.generation_seconds || 0),
        audio_s: Number(result.duration || 0),
        rtf: result.rtf == null ? null : Number(result.rtf),
        backend: String(result.device || result.engine || ''),
      };
    }
    return result;
  } catch (err) {
    return { ok: false, error: err.message || String(err) };
  }
}

function pumpStreamingSynthesis(stream) {
  if (!stream || stream.cancelled || stream.run !== speechRun || stream.synthesizing) return;
  const text = stream.chunks.shift();
  if (!text) {
    resolveSpeechStreamIfFinished(stream);
    return;
  }
  stream.synthesizing = true;
  requestStreamingSpeechChunk(text).then((result) => {
    if (stream.cancelled || stream.run !== speechRun) return;
    if (!result?.ok) {
      if (!stream.warned) {
        toast('Синтез речи не удался', result?.error || 'Q.W.A.N.T.A. не смогла построить голос.', 'error', 6000);
        stream.warned = true;
      }
      return;
    }
    stream.ready.push(result);
    pumpStreamingPlayback(stream);
  }).finally(() => {
    stream.synthesizing = false;
    if (!stream.cancelled && stream.run === speechRun) pumpStreamingSynthesis(stream);
    resolveSpeechStreamIfFinished(stream);
  });
}

function pumpStreamingPlayback(stream) {
  if (!stream || stream.cancelled || stream.run !== speechRun || stream.playing) return;
  const info = stream.ready.shift();
  if (!info) {
    resolveSpeechStreamIfFinished(stream);
    return;
  }
  stream.playing = true;
  setOrbState('speaking');
  playSpeechChunk(info.url || '', stream.run).then(() => {
    stream.playing = false;
    if (!stream.cancelled && stream.run === speechRun && !stream.finalized) setOrbState('thinking');
    pumpStreamingPlayback(stream);
    resolveSpeechStreamIfFinished(stream);
  });
}

function finishStreamingSpeech(text) {
  const stream = streamingSpeech;
  if (!stream || stream.cancelled || stream.run !== speechRun) return null;
  const finalText = String(text || '');
  if (finalText && !stream.sourceText) {
    stream.sourceText = finalText;
    stream.buffer = finalText;
  } else if (finalText.startsWith(stream.sourceText)) {
    const missing = finalText.slice(stream.sourceText.length);
    stream.sourceText += missing;
    stream.buffer += missing;
  }
  drainStreamingSpeechBuffer(stream, true);
  stream.finalized = true;
  pumpStreamingSynthesis(stream);
  pumpStreamingPlayback(stream);
  resolveSpeechStreamIfFinished(stream);
  return stream.finished;
}

function splitSpeechChunks(text) {
  const value = String(text || '').replace(/\s+/g, ' ').trim();
  if (!value) return [];
  const sentences = value.match(/[^.!?…]+[.!?…]+[»")]*|[^.!?…]+$/g) || [value];
  const chunks = [];
  let current = '';
  for (const raw of sentences) {
    const sentence = String(raw || '').trim();
    if (!sentence) continue;
    if (!current) {
      current = sentence;
    } else if (current.length + sentence.length + 1 <= 220) {
      current += ' ' + sentence;
    } else {
      chunks.push(current);
      current = sentence;
    }
  }
  if (current) chunks.push(current);
  return chunks;
}

function playSpeechChunk(url, run) {
  return new Promise((resolve) => {
    const audio = document.createElement('audio');
    currentSpeechAudio = audio;
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      if (currentSpeechAudio === audio) currentSpeechAudio = null;
      resolve();
    };
    audio.src = url || '';
    audio.preload = 'auto';
    audio.onended = finish;
    audio.onerror = finish;
    audio.onpause = () => { if (run !== speechRun) finish(); };
    const played = audio.play();
    if (played && typeof played.catch === 'function') {
      played.catch(() => {
        if (run === speechRun) {
          toast('Браузер заблокировал звук', 'Кликни по окну Кванты и попроси повторить.', 'warn', 6000);
        }
        finish();
      });
    }
  });
}

async function speakAssistantResponse(text) {
  const settings = state.settings;
  if (!settings?.voice_enabled || !text) return;
  const streaming = finishStreamingSpeech(text);
  if (streaming) {
    await streaming;
    return;
  }
  stopSpeech();
  const run = speechRun;
  const chunks = splitSpeechChunks(text);
  if (!chunks.length) return;
  setOrbState('speaking');
  const requestChunk = async (index) => requestStreamingSpeechChunk(chunks[index]);
  let pending = null;
  let warned = false;
  for (let index = 0; index < chunks.length; index += 1) {
    if (run !== speechRun) return;
    const info = pending ? await pending : await requestChunk(index);
    pending = null;
    if (run !== speechRun) return;
    if (!info?.ok) {
      if (!warned) {
        toast('Синтез речи не удался', info?.error || 'Q.W.A.N.T.A. не смогла построить голос.', 'error', 6000);
        warned = true;
      }
      continue;
    }
    if (index + 1 < chunks.length) pending = requestChunk(index + 1);
    await playSpeechChunk(info.url || '', run);
  }
  if (run === speechRun) setOrbState('idle');
}
const notified = new Set();

async function pollEvents() {
  try {
    const data = await api('/api/events');
    for (const event of data.events || []) {
      if (event.type === 'reminder') {
        const reminder = event.reminder || {};
        if (notified.has(reminder.id)) continue;
        notified.add(reminder.id);
        toast('Напоминание', reminder.text || '', 'warn', 12000);
        beep();
        await refreshReminders();
      } else if (event.message) {
        toast('Q.W.A.N.T.A.', event.message, 'info', 6000);
      }
    }
  } catch { /* server may be restarting */ }
}

/* ------------------------------------------------------------------ orb/clock */
function setOrbState(value) {
  state.orbState = value || 'idle';
  document.body.dataset.orb = state.orbState;
  const labels = {
    idle: { title: 'Свободна', detail: 'На связи и готова к разговору', activity: 'Можно начать новый разговор или продолжить текущий.', caption: 'НА СВЯЗИ' },
    thinking: { title: 'Обдумывает', detail: 'Собирает ответ по контексту', activity: state.activeTask || 'Обдумывает ответ…', caption: 'ДУМАЕТ' },
    tool: { title: 'В работе', detail: 'Выполняет действие', activity: state.activeTask || 'Выполняет действие…', caption: 'В РАБОТЕ' },
    permission: { title: 'Ждёт решения', detail: 'Для действия нужно подтверждение', activity: state.activeTask || 'Ожидает разрешение пользователя.', caption: 'ЖДЁТ РЕШЕНИЯ' },
    speaking: { title: 'Говорит', detail: 'Отвечает голосом', activity: 'Озвучивает ответ.', caption: 'ГОВОРИТ' },
    listening: { title: 'Слушает', detail: 'Голосовой ввод активен', activity: 'Можно говорить — Кванта записывает.', caption: 'СЛУШАЕТ' },
  };
  const current = labels[state.orbState] || labels.idle;
  if (dom.orbCaption) dom.orbCaption.textContent = current.caption;
  if (dom.presenceState) dom.presenceState.textContent = current.title;
  if (dom.presenceDetail) dom.presenceDetail.textContent = current.detail;
  if (dom.presenceActivity) dom.presenceActivity.textContent = current.activity;
}

function startOrb() {
  const canvas = dom.orbCanvas;
  if (!canvas) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = 300 * dpr;
  canvas.height = 300 * dpr;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.scale(dpr, dpr);

  let frame = 0;
  let lastFrame = 0;
  const draw = (now) => {
    frame = 0;
    if (document.hidden) return;
    if (now - lastFrame < 1000 / 20) {
      frame = requestAnimationFrame(draw);
      return;
    }
    lastFrame = now;
    const t = now / 1000;
    const speed = { idle: .18, thinking: .85, tool: 1.25, speaking: .55, listening: .35 }[state.orbState] || .18;
    const energy = state.orbState === 'idle' ? 0.45 : 1;
    ctx.clearRect(0, 0, 300, 300);
    const cx = 150;
    const cy = 150;

    const glow = ctx.createRadialGradient(cx, cy, 8, cx, cy, 132);
    glow.addColorStop(0, `rgba(184,167,242,${0.11 * energy})`);
    glow.addColorStop(0.45, 'rgba(184,167,242,0.045)');
    glow.addColorStop(1, 'rgba(20,20,20,0)');
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(cx, cy, 132, 0, Math.PI * 2);
    ctx.fill();

    ctx.save();
    ctx.translate(cx, cy);
    ctx.globalCompositeOperation = 'source-over';

    const rings = [
      { r: 103, dash: [2, 9], speed: .12, color: 'rgba(214,207,230,.26)', width: 1 },
      { r: 82, dash: [26, 12], speed: -.2, color: 'rgba(184,167,242,.38)', width: 1.2 },
      { r: 62, dash: [1, 6], speed: .36, color: 'rgba(231,201,173,.34)', width: 1 },
    ];
    rings.forEach((ring, index) => {
      ctx.save();
      ctx.rotate(t * ring.speed * (1 + speed) + index);
      ctx.strokeStyle = ring.color;
      ctx.lineWidth = ring.width;
      ctx.setLineDash(ring.dash);
      ctx.shadowColor = index === 1 ? 'rgba(184,167,242,.28)' : 'rgba(231,201,173,.2)';
      ctx.shadowBlur = 5;
      ctx.beginPath();
      ctx.arc(0, 0, ring.r, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    });

    ctx.setLineDash([]);
    for (let arcIndex = 0; arcIndex < 3; arcIndex += 1) {
      const radius = 73 + arcIndex * 9;
      const start = t * (0.7 + speed * 0.8) * (arcIndex % 2 ? -1 : 1) + arcIndex * 2.1;
      ctx.strokeStyle = arcIndex === 1 ? 'rgba(231,201,173,.4)' : 'rgba(184,167,242,.42)';
      ctx.lineWidth = 1.6;
      ctx.shadowColor = arcIndex === 1 ? 'rgba(231,201,173,.25)' : 'rgba(184,167,242,.28)';
      ctx.shadowBlur = 6;
      ctx.beginPath();
      ctx.arc(0, 0, radius, start, start + 0.7 + speed * 0.35);
      ctx.stroke();
    }

    const particles = 12;
    for (let i = 0; i < particles; i += 1) {
      const angle = t * (0.09 + speed * 0.38) + i * (Math.PI * 2 / particles);
      const radius = 58 + (i % 4) * 16 + Math.sin(t * 0.7 + i) * 4;
      const x = Math.cos(angle) * radius;
      const y = Math.sin(angle) * radius;
      ctx.fillStyle = i === 0 ? 'rgba(231,201,173,.65)' : 'rgba(184,167,242,.58)';
      ctx.shadowColor = i === 0 ? 'rgba(231,201,173,.3)' : 'rgba(184,167,242,.3)';
      ctx.shadowBlur = 5;
      ctx.beginPath();
      ctx.arc(x, y, i % 5 === 0 ? 1.8 : 1.1, 0, Math.PI * 2);
      ctx.fill();
    }

    const pulse = 1 + Math.sin(t * (1.2 + speed * 2)) * 0.08;
    const core = ctx.createRadialGradient(-5, -7, 1, 0, 0, 27 * pulse);
    core.addColorStop(0, '#fffdf8');
    core.addColorStop(0.42, '#c9baf5');
    core.addColorStop(1, 'rgba(151,132,207,.82)');
    ctx.fillStyle = core;
    ctx.shadowColor = 'rgba(184,167,242,.42)';
    ctx.shadowBlur = 18 * energy;
    ctx.beginPath();
    ctx.arc(0, 0, 20 * pulse, 0, Math.PI * 2);
    ctx.fill();

    ctx.restore();
    frame = requestAnimationFrame(draw);
  };
  const resume = () => {
    if (!document.hidden && !frame) frame = requestAnimationFrame(draw);
  };
  document.addEventListener('visibilitychange', resume, { passive: true });
  resume();
}

function updateClock() {
  const now = new Date();
  if (dom.clockTime) {
    dom.clockTime.textContent = now.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  }
  if (dom.clockDate) {
    const text = now.toLocaleDateString('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' });
    dom.clockDate.textContent = text.charAt(0).toUpperCase() + text.slice(1);
  }
}

/* ------------------------------------------------------------------ boot/init */
function setBootProgress(percent) {
  if (dom.bootBar) dom.bootBar.style.width = `${clamp(percent, 0, 100)}%`;
}

function hideBoot(immediate = false) {
  if (state.bootDone) return;
  state.bootDone = true;
  if (state.bootTimer) clearInterval(state.bootTimer);
  if (!dom.boot) return;
  dom.boot.classList.add('done');
  if (immediate) {
    dom.boot.style.display = 'none';
  } else {
    setTimeout(() => { if (dom.boot) dom.boot.style.display = 'none'; }, 220);
  }
}

function bootError(err) {
  if (state.bootTimer) clearInterval(state.bootTimer);
  if (dom.bootLog) dom.bootLog.textContent = `Сервер не отвечает: ${err.message || err}`;
  if (dom.bootBar) {
    dom.bootBar.style.width = '100%';
    dom.bootBar.style.background = 'linear-gradient(90deg,#fb7185,#8b5cf6)';
  }
  if (dom.bootSkip) {
    dom.bootSkip.textContent = 'нажми, чтобы повторить';
    dom.bootSkip.onclick = () => location.reload();
  }
}

function loadBootSequence() {
  const skip = params.get('noboot') === '1';
  if (skip) {
    setBootProgress(100);
    hideBoot(true);
    return;
  }
  if (dom.bootLog) dom.bootLog.textContent = 'Подключаюсь…';
  setBootProgress(36);
}

function autoGrowInput() {
  if (!dom.input) return;
  dom.input.style.height = 'auto';
  dom.input.style.height = `${Math.min(dom.input.scrollHeight, 180)}px`;
}

function handlePreset(prompt) {
  if (!prompt) return;
  if (state.busy) {
    toast('Кванта занята', 'Дождись ответа или останови генерацию.', 'warn', 2400);
    return;
  }
  dom.input.value = prompt;
  autoGrowInput();
  closeSidebar();
  sendMessage();
}

/* ------------------------------------------------------------------ binding */
function bindEvents() {
  dom.bootSkip?.addEventListener('click', () => hideBoot(true));
  dom.sendBtn?.addEventListener('click', sendMessage);
  dom.stopBtn?.addEventListener('click', stopGeneration);
  dom.newChatBtn?.addEventListener('click', newChat);
  dom.drawerClose?.addEventListener('click', closeDrawer);
  dom.settingsBtn?.addEventListener('click', openSettings);
  dom.seriousModeBtn?.addEventListener('click', () => setSeriousMode(!state.seriousMode));
  dom.sidebarToggle?.addEventListener('click', () => dom.app?.classList.toggle('sidebar-open'));

  dom.input?.addEventListener('keydown', (event) => {
    if (event.isComposing) return;
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });
  dom.input?.addEventListener('input', autoGrowInput);

  dom.accessSelect?.addEventListener('change', async () => {
    const previous = state.settings?.access_mode || 'smart';
    const mode = dom.accessSelect.value;
    try {
      await saveSettings({ access_mode: mode }, { silent: true });
      setModeChip(mode);
      toast('Режим доступа', MODE_LABELS[mode] || mode, 'success', 2200);
    } catch (err) {
      dom.accessSelect.value = previous;
      toast('Режим не изменён', err.message, 'error');
    }
  });

  dom.resourceProfileSelect?.addEventListener('change', async () => {
    const previous = state.settings?.resource_profile || 'work';
    const profile = dom.resourceProfileSelect.value;
    dom.resourceProfileSelect.disabled = true;
    try {
      const data = await api('/api/resource-profile', {
        method: 'POST',
        body: JSON.stringify({ profile }),
      });
      state.settings = data.settings || state.settings;
      applySettingsToUI();
      toast(profile === 'game' ? 'Игровой профиль' : 'Рабочий профиль', 'Переключаю модели в фоне', 'info', 2500);
      const finalState = await watchResourceTransition();
      if (!finalState) {
        toast('Переключение ещё идёт', dom.resourceProfileSelect?.title || '', 'info', 3000);
      } else if (finalState.status === 'error') {
        toast('Профиль включён с проблемой', finalState.message || '', 'warn', 4500);
      } else {
        toast(profile === 'game' ? 'Игровой профиль включён' : 'Рабочий профиль включён', finalState.message || '', 'success', 3000);
      }
    } catch (err) {
      dom.resourceProfileSelect.value = previous;
      dom.resourceProfileSelect.disabled = false;
      toast('Профиль не изменён', err.message, 'error');
    }
  });

  dom.modelSelect?.addEventListener('change', async () => {
    const previous = state.settings?.model;
    try {
      await saveSettings({ model: dom.modelSelect.value }, { silent: true });
      toast('Модель переключена', dom.modelSelect.value, 'success', 2200);
    } catch (err) {
      if (previous) dom.modelSelect.value = previous;
      toast('Модель не изменена', err.message, 'error');
    }
  });

  $$('.view-switch [data-view]').forEach((button) => {
    button.addEventListener('click', () => setMode(button.dataset.view));
  });

  dom.expandBtn?.addEventListener('click', async () => {
    try {
      await api('/api/window?view=hud');
      toast('Разворачиваю HUD', 'Полное окно откроется отдельно.', 'info', 1800);
      setTimeout(() => { try { window.close(); } catch { /* ignore */ } }, 500);
    } catch (err) {
      setMode('hud', { persist: false });
      toast('HUD включён в этом окне', err.message, 'warn', 3000);
    }
  });

  dom.micBtn?.addEventListener('click', async () => {
    if (state.recording) {
      await stopRecording();
    } else {
      await startRecording();
    }
  });

  document.addEventListener('click', (event) => {
    const promptButton = event.target.closest('[data-prompt]');
    if (promptButton) {
      event.preventDefault();
      handlePreset(promptButton.dataset.prompt || '');
      return;
    }
    const openButton = event.target.closest('[data-open]');
    if (openButton) {
      event.preventDefault();
      openDrawer(openButton.dataset.open);
      return;
    }
    const copyButton = event.target.closest('.code-head button');
    if (copyButton) {
      const code = copyButton.closest('.code-block')?.querySelector('code')?.innerText || '';
      navigator.clipboard?.writeText(code).then(() => {
        copyButton.textContent = 'скопировано';
        setTimeout(() => { copyButton.textContent = 'копировать'; }, 1300);
      }).catch(() => {});
    }
  });

  dom.drawerBody?.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-action]');
    if (!button) return;
    const action = button.dataset.action;
    const id = button.dataset.id || '';
    if (action === 'new-note') openNoteEditor();
    else if (action === 'refresh-notes') refreshNotes();
    else if (action === 'edit-note') openNoteEditor(state.notes.find((note) => note.id === id));
    else if (action === 'delete-note') deleteNote(id);
    else if (action === 'new-project') { state.editingProjectId = '__new__'; renderProjectsDrawer(); }
    else if (action === 'edit-project') { state.editingProjectId = id; renderProjectsDrawer(); }
    else if (action === 'cancel-project-edit') { state.editingProjectId = ''; renderProjectsDrawer(); }
    else if (action === 'link-project-chat') linkProjectChat(id, true);
    else if (action === 'unlink-project-chat') linkProjectChat(id, false);
    else if (action === 'archive-project') setProjectArchived(id, true);
    else if (action === 'restore-project') setProjectArchived(id, false);
    else if (action === 'open-project-chat') openProjectChat(button.dataset.chatId || '');
    else if (action === 'refresh-reminders') refreshReminders();
    else if (action === 'done-reminder') markReminderDone(id);
    else if (action === 'delete-reminder') deleteReminder(id);
    else if (action === 'delete-memory') forgetMemory(id);
    else if (action === 'delete-workflow') deleteWorkflow(id);
    else if (action === 'run-workflow') runWorkflow(id);
    else if (action === 'undo-action') undoAction(id);
    else if (action === 'clear-actions') clearActionHistory();
    else if (action === 'refresh-actions') refreshActions();
    else if (action === 'refresh-diagnostics') renderDiagnosticsDrawer();
    else if (action === 'open-settings') {
      closeDrawer();
      openSettings();
    }
  });

  dom.drawerBody?.addEventListener('submit', async (event) => {
    const formId = event.target.id;
    if (!['reminderForm', 'memoryForm', 'workflowForm', 'projectForm'].includes(formId)) return;
    event.preventDefault();
    if (formId === 'projectForm') {
      await saveProjectForm(event.target);
    } else if (formId === 'reminderForm') {
      const text = dom.drawerBody.querySelector('#reminderText')?.value.trim();
      const when = dom.drawerBody.querySelector('#reminderWhen')?.value;
      if (text && when) await createReminder(text, when);
    } else if (formId === 'memoryForm') {
      const text = dom.drawerBody.querySelector('#memoryText')?.value.trim();
      if (text) await saveMemory(text);
    } else {
      const title = dom.drawerBody.querySelector('#workflowTitle')?.value.trim();
      const prompt = dom.drawerBody.querySelector('#workflowPrompt')?.value.trim();
      if (!title || !prompt) return;
      try {
        await api('/api/workflows', { method: 'POST', body: JSON.stringify({ title, prompt }) });
        await refreshWorkflows();
        toast('Сценарий сохранён', title, 'success', 2200);
      } catch (err) { toast('Сценарий не сохранён', err.message, 'error'); }
    }
  });

  document.addEventListener('keydown', (event) => {
    if (event.ctrlKey && event.code === 'Space') {
      event.preventDefault();
      dom.input?.focus();
      return;
    }
    if (event.ctrlKey && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      newChat();
      return;
    }
    if (event.key === 'Escape') {
      if (modalClosers.length) {
        closeModals();
      } else if (!dom.drawer?.classList.contains('hidden')) {
        closeDrawer();
      }
    }
  });

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      pollHealth();
      pollEvents();
      refreshChats();
      syncCurrentChat(false);
      if (state.settings?.voice_enabled) loadVoiceStatus();
    }
  });
  window.addEventListener('focus', () => {
    if (!document.hidden && !state.busy) syncCurrentChat(false);
  });
}

async function init() {
  cacheDom();
  bindEvents();
  startOrb();
  loadBootSequence();
  try {
    const data = await api('/api/bootstrap');
    state.settings = data.settings;
    state.chats = data.chats || [];
    state.notes = data.notes || [];
    state.reminders = data.reminders || [];
    state.dataDir = data.data_dir || '';
    state.version = data.version || '0.1.0';
    state.system = data.system || null;
    applySettingsToUI();
    let seriousMode = false;
    try { seriousMode = localStorage.getItem('quanta:tone') === 'serious'; } catch { /* storage may be disabled */ }
    setSeriousMode(seriousMode, { persist: false });
    updateStatus(data.health);
    applySystem(data.system);
    renderChats();
    const initialMode = ['mini', 'standard', 'hud'].includes(params.get('view'))
      ? params.get('view')
      : (data.settings.ui_mode || localStorage.getItem('quanta:view') || 'standard');
    setMode(initialMode, { persist: false });
    const initialChat = params.get('chat') || localStorage.getItem('quanta:chat');
    if (initialChat && state.chats.some((chat) => chat.id === initialChat)) {
      await openChat(initialChat, { silent: true });
    } else {
      state.messages = [];
      renderMessages();
    }
    loadModels(false);
    refreshWorkflows();
    loadVoiceStatus();
    if (state.bootTimer) {
      clearInterval(state.bootTimer);
      state.bootTimer = null;
    }
    setBootProgress(100);
    if (dom.bootLog) dom.bootLog.textContent = 'Ядро в сети.';
    setTimeout(() => hideBoot(false), 120);
    setInterval(() => { if (!document.hidden) pollHealth(); }, 30000);
    setInterval(() => { if (!document.hidden) pollEvents(); }, 8000);
    setInterval(() => { if (!document.hidden && state.settings?.voice_enabled) loadVoiceStatus(); }, 15000);
    setInterval(() => { if (!document.hidden) refreshChats(); }, 25000);
    setInterval(() => { if (!document.hidden) syncCurrentChat(false); }, 20000);
    setTimeout(() => dom.input?.focus(), 400);
  } catch (err) {
    bootError(err);
    toast('Нет связи с сервером', err.message || String(err), 'error', 9000);
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}




