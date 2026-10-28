// WhisperJAV web UI. Vanilla ES module, no build step, no dependencies.

// ---------------------------------------------------------------- http
const api = {
  async request(method, path, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    if (res.status === 204) return null;
    let data = null;
    try { data = await res.json(); } catch (_) { /* empty body */ }
    if (!res.ok) {
      const msg = (data && (data.detail || data.error)) || `HTTP ${res.status}`;
      throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    }
    return data;
  },
  get(p) { return this.request('GET', p); },
  post(p, b) { return this.request('POST', p, b); },
};

const $ = (id) => document.getElementById(id);
const qp = (obj) => Object.entries(obj)
  .filter(([, v]) => v !== undefined && v !== null && v !== '')
  .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');

// ---------------------------------------------------------------- state
const S = {
  config: null,
  flow: 'transcribe',
  inputs: [],
  jobId: null,
  since: 0,
  timer: null,
  logCount: 0,
  browserTarget: null,   // null = add to inputs; otherwise an input element id
  browserPath: null,
  browserSelection: new Set(),
};

const STORE_KEY = 'whisperjav-web-form';
const TRANSCRIBE_FIELDS = ['t_mode', 't_sensitivity', 't_language', 't_subs_language',
  't_output_format', 't_log_level', 't_model', 't_output_dir', 't_temp_dir',
  't_keep_temp', 't_skip_existing', 't_accept_cpu_mode'];
const TRANSLATE_FIELDS = ['x_provider', 'x_target', 'x_source', 'x_tone', 'x_model',
  'x_endpoint', 'x_ollama_url', 'x_ollama_num_ctx', 'x_scene_threshold',
  'x_max_batch_size', 'x_include_original', 'x_movie_title', 'x_actress', 'x_movie_plot'];

// ---------------------------------------------------------------- boot
async function init() {
  S.config = await api.get('/api/config');
  $('versionLabel').textContent = 'v' + S.config.version;
  fillSelects();
  restoreForm();
  applyFlow(S.flow);
  bind();

  const { jobs } = await api.get('/api/jobs');
  const live = jobs.find((j) => j.state === 'running' || j.state === 'queued');
  if (live) attach(live.id);
  else if (jobs.length) renderTerminal(jobs[0]);
}

function fillOptions(el, values, selected) {
  el.innerHTML = '';
  for (const v of values) {
    const opt = document.createElement('option');
    opt.value = v; opt.textContent = v;
    if (v === selected) opt.selected = true;
    el.appendChild(opt);
  }
}

function fillSelects() {
  const t = S.config.transcribe, x = S.config.translate, saved = S.config.saved || {};
  fillOptions($('t_mode'), t.mode, 'balanced');
  fillOptions($('t_sensitivity'), t.sensitivity, 'balanced');
  fillOptions($('t_language'), t.language, 'japanese');
  fillOptions($('t_subs_language'), t.subs_language, 'native');
  fillOptions($('t_output_format'), t.output_format, 'srt');
  fillOptions($('t_log_level'), t.log_level, 'INFO');
  $('t_output_dir').placeholder = '留空 = 与源文件同目录；常用：' + S.config.defaults.output_dir;

  fillOptions($('x_provider'), x.providers.map((p) => p.id), saved.provider || 'ollama');
  fillOptions($('x_target'), x.targets, saved.target_language || 'english');
  fillOptions($('x_source'), x.sources, 'japanese');
  fillOptions($('x_tone'), x.tones, saved.tone || 'standard');
  // settings.json holds one global "model" that outranks the provider default
  // (see _resolve_model in whisperjav/translate/service.py).
  if (saved.model) $('x_model').value = saved.model;
  if (saved.include_original) $('x_include_original').checked = true;
  if (saved.ollama_url) $('x_ollama_url').value = saved.ollama_url;
  if (saved.scene_threshold) $('x_scene_threshold').value = saved.scene_threshold;
  if (saved.max_batch_size) $('x_max_batch_size').value = saved.max_batch_size;
  updateKeyHint();
}

function updateKeyHint() {
  const id = $('x_provider').value;
  const p = (S.config.translate.providers || []).find((x) => x.id === id);
  if (!p) return;

  // Report what will ACTUALLY be used, following the same precedence as
  // _resolve_model: form field > settings.json > provider default.
  const savedModel = (S.config.saved || {}).model;
  const typed = $('x_model').value.trim();
  let model = '';
  if (typed) {
    model = `将使用 ${typed}（本次输入）。`;
  } else if (savedModel) {
    model = `将使用 ${savedModel}（来自已保存设置，覆盖内置默认 ${p.default_model || '—'}）。`;
  } else if (p.default_model) {
    model = `将使用 ${p.default_model}（provider 内置默认）。`;
  }

  if (!p.needs_key) {
    $('keyHint').textContent = `${model}该 provider 不需要 API Key。`;
  } else if (p.key_present) {
    $('keyHint').textContent = `${model}已检测到环境变量 ${p.env_var}，可留空。`;
  } else {
    $('keyHint').textContent = `${model}未检测到 ${p.env_var}，必须在上方填写 API Key。`;
  }
}

// ---------------------------------------------------------------- form
function applyFlow(flow) {
  S.flow = flow;
  document.documentElement.dataset.flow = flow;
  for (const el of document.querySelectorAll('input[name=flow]')) {
    el.checked = el.value === flow;
  }
}

function persistForm() {
  const data = { flow: S.flow, inputs: S.inputs };
  for (const id of [...TRANSCRIBE_FIELDS, ...TRANSLATE_FIELDS]) {
    const el = $(id);
    if (el) data[id] = el.type === 'checkbox' ? el.checked : el.value;
  }
  // x_api_key is deliberately absent: never persisted.
  try { localStorage.setItem(STORE_KEY, JSON.stringify(data)); } catch (_) { }
}

function restoreForm() {
  let data;
  try { data = JSON.parse(localStorage.getItem(STORE_KEY) || '{}'); } catch (_) { return; }
  if (data.flow) S.flow = data.flow;
  if (Array.isArray(data.inputs)) { S.inputs = data.inputs; renderInputs(); }
  for (const id of [...TRANSCRIBE_FIELDS, ...TRANSLATE_FIELDS]) {
    if (!(id in data)) continue;
    const el = $(id);
    if (!el) continue;
    if (el.type === 'checkbox') el.checked = !!data[id];
    else el.value = data[id];
  }
  updateKeyHint();
}

const num = (id) => { const v = $(id).value.trim(); return v === '' ? null : Number(v); };
const str = (id) => { const v = $(id).value.trim(); return v === '' ? null : v; };

function readPayload() {
  return {
    flow: S.flow,
    inputs: S.inputs,
    transcribe: {
      mode: $('t_mode').value,
      model: str('t_model'),
      language: $('t_language').value,
      subs_language: $('t_subs_language').value,
      sensitivity: $('t_sensitivity').value,
      output_dir: str('t_output_dir'),
      output_format: $('t_output_format').value,
      temp_dir: str('t_temp_dir'),
      keep_temp: $('t_keep_temp').checked,
      skip_existing: $('t_skip_existing').checked,
      accept_cpu_mode: $('t_accept_cpu_mode').checked,
      log_level: $('t_log_level').value,
    },
    translate: {
      provider: $('x_provider').value,
      target: $('x_target').value,
      source: $('x_source').value,
      tone: $('x_tone').value,
      model: str('x_model'),
      api_key: str('x_api_key'),
      endpoint: str('x_endpoint'),
      include_original: $('x_include_original').checked ? true : null,
      scene_threshold: num('x_scene_threshold'),
      max_batch_size: num('x_max_batch_size'),
      ollama_url: str('x_ollama_url'),
      ollama_num_ctx: num('x_ollama_num_ctx'),
      movie_title: str('x_movie_title'),
      actress: str('x_actress'),
      movie_plot: str('x_movie_plot'),
    },
  };
}

// ---------------------------------------------------------------- inputs
function renderInputs() {
  const ul = $('inputList');
  ul.innerHTML = '';
  for (const path of S.inputs) {
    const li = document.createElement('li');
    const span = document.createElement('span');
    span.textContent = path;
    const btn = document.createElement('button');
    btn.type = 'button'; btn.textContent = '×'; btn.title = '移除';
    btn.onclick = () => {
      S.inputs = S.inputs.filter((p) => p !== path);
      renderInputs(); persistForm();
    };
    li.append(span, btn);
    ul.appendChild(li);
  }
  $('inputSummary').textContent = S.inputs.length
    ? `已选 ${S.inputs.length} 项` : '未选择';
}

function addInputs(paths) {
  for (const p of paths) if (!S.inputs.includes(p)) S.inputs.push(p);
  renderInputs(); persistForm();
}

// ---------------------------------------------------------------- browser
const browserKind = () => (S.browserTarget ? 'all'
  : (S.flow === 'translate' ? 'srt' : 'media'));

async function openBrowser(targetInputId) {
  S.browserTarget = targetInputId || null;
  S.browserSelection.clear();
  const start = targetInputId
    ? ($(targetInputId).value.trim() || S.config.defaults.output_dir)
    : (S.browserPath || S.config.defaults.home);
  $('browser').showModal();
  await navigate(start);
}

async function navigate(path) {
  let data;
  try {
    data = await api.get('/api/browse?' + qp({ path, kind: browserKind() }));
  } catch (err) {
    $('dlgStatus').textContent = err.message;
    return;
  }
  S.browserPath = data.path;
  $('pathInput').value = data.path || '';
  $('btnUp').disabled = data.path === null;
  $('btnAddFolder').disabled = data.path === null || !!S.browserTarget;

  const drives = $('driveSelect');
  drives.innerHTML = '<option value="">此电脑</option>';
  for (const r of data.roots) {
    const o = document.createElement('option');
    o.value = r.path; o.textContent = r.label;
    if (data.path && data.path.toUpperCase().startsWith(r.path.toUpperCase())) {
      o.selected = true;
    }
    drives.appendChild(o);
  }

  const sc = $('shortcuts');
  sc.innerHTML = '';
  for (const s of data.shortcuts) {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'ghost'; b.textContent = s.label;
    b.onclick = () => navigate(s.path);
    sc.appendChild(b);
  }

  const ul = $('entries');
  ul.innerHTML = '';
  if (data.path === null) {
    for (const r of data.roots) ul.appendChild(dirRow(r.label, r.path));
  } else {
    for (const d of data.dirs) ul.appendChild(dirRow(d.name, d.path));
    if (!S.browserTarget) for (const f of data.files) ul.appendChild(fileRow(f));
  }

  const parts = [];
  if (data.path !== null) {
    parts.push(`${data.dirs.length} 个目录`);
    if (!S.browserTarget) parts.push(`${data.files.length} 个文件`);
  }
  if (data.truncated) parts.push('（已截断到 2000 条）');
  $('dlgStatus').textContent = parts.join(' · ');
  $('browser').dataset.parent = data.parent === null ? '' : data.parent;
}

function dirRow(name, path) {
  const li = document.createElement('li');
  li.className = 'dir';
  const n = document.createElement('span');
  n.className = 'name'; n.textContent = '📁 ' + name;
  li.appendChild(n);
  li.onclick = () => navigate(path);
  return li;
}

function fileRow(f) {
  const li = document.createElement('li');
  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.checked = S.browserSelection.has(f.path);
  cb.onchange = () => {
    if (cb.checked) S.browserSelection.add(f.path);
    else S.browserSelection.delete(f.path);
  };
  const n = document.createElement('span');
  n.className = 'name'; n.textContent = f.name;
  n.onclick = () => { cb.checked = !cb.checked; cb.onchange(); };
  const s = document.createElement('span');
  s.className = 'size'; s.textContent = humanSize(f.size);
  li.append(cb, n, s);
  return li;
}

function humanSize(bytes) {
  if (!bytes) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0, v = bytes;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
}

// ---------------------------------------------------------------- submit
async function onStart() {
  $('submitError').textContent = '';
  try {
    const res = await api.post('/api/jobs', readPayload());
    persistForm();
    attach(res.id);
  } catch (err) {
    $('submitError').textContent = err.message;
  }
}

async function onCancel() {
  if (!S.jobId) return;
  try { await api.post(`/api/jobs/${S.jobId}/cancel`); } catch (err) {
    $('submitError').textContent = err.message;
  }
}

// ---------------------------------------------------------------- polling
function attach(jobId) {
  if (S.timer) clearTimeout(S.timer);
  S.jobId = jobId;
  S.since = 0;
  S.logCount = 0;
  $('log').textContent = '';
  $('progressCard').hidden = false;
  tick();
}

function nextDelay(state) {
  if (document.visibilityState === 'hidden') return 5000;
  return state === 'running' ? 500 : 2000;
}

async function tick() {
  if (!S.jobId) return;
  let job;
  try {
    job = await api.get(`/api/jobs/${S.jobId}?with_logs=${S.since}`);
  } catch (err) {
    $('submitError').textContent = err.message;
    S.timer = setTimeout(tick, 3000);
    return;
  }

  if (job.dropped > 0) appendLines([`--- 已丢弃 ${job.dropped} 行更早日志 ---`]);
  if (job.lines && job.lines.length) appendLines(job.lines);
  S.since = job.next_since ?? S.since;

  renderProgress(job);

  const terminal = ['completed', 'failed', 'cancelled'].includes(job.state);
  if (terminal) { renderTerminal(job); return; }
  S.timer = setTimeout(tick, nextDelay(job.state));
}

const STATE_LABEL = {
  queued: '排队中', running: '运行中', completed: '已完成',
  failed: '失败', cancelled: '已取消', idle: '空闲',
};

function renderProgress(job) {
  const p = job.progress || {};
  const pill = $('statePill');
  pill.className = 'pill ' + job.state;
  pill.textContent = STATE_LABEL[job.state] || job.state;

  $('btnStart').disabled = job.state === 'running' || job.state === 'queued';
  $('btnCancel').disabled = !(job.state === 'running' || job.state === 'queued');

  $('overallBar').style.width = (p.overall_pct || 0) + '%';
  $('overallPct').textContent = (p.overall_pct || 0).toFixed(0) + '%';
  $('phaseBadge').textContent = p.phase === 'translate' ? '翻译中' : '转写中';

  $('fileLine').textContent = p.file_total > 1 || p.file_name
    ? `文件 ${p.file_index || 0}/${p.file_total || 1}${p.file_name ? ' · ' + p.file_name : ''}`
    : '';
  $('elapsedLine').textContent = `已用 ${fmtSeconds(job.elapsed)}`;

  if (p.phase === 'translate') {
    const t = p.translate || {};
    $('stepLine').textContent = t.batches_total
      ? `翻译批次 ${t.batch_done}/${t.batches_total}`
      : (t.scenes_total ? `翻译场景 ${t.scene_done}/${t.scenes_total}` : '翻译中…');
    $('taskBar').style.width = (t.pct || 0) + '%';
    $('taskLine').textContent = t.scenes_total ? `共 ${t.scenes_total} 个场景` : '';
  } else {
    $('stepLine').textContent = p.step_name
      ? `Step ${p.step_index}/${p.step_total}: ${p.step_name}` : '';
    $('taskBar').style.width = (p.task_pct || 0) + '%';
    $('taskLine').textContent = p.task_total
      ? `${p.task_name} ${p.task_current}/${p.task_total}` +
        (p.elapsed_str ? ` · 已用 ${p.elapsed_str}` : '') +
        (p.eta_str ? ` · 剩余 ${p.eta_str}` : '')
      : '';
  }

  $('lastMessage').textContent = p.last_message || '';
  renderOutputs(job.output_files || []);
}

function renderTerminal(job) {
  renderProgress(job);
  $('btnStart').disabled = false;
  $('btnCancel').disabled = true;
  if (job.error) $('submitError').textContent = job.error;
}

function renderOutputs(files) {
  const ul = $('outputList');
  if (ul.childElementCount === files.length) return;
  ul.innerHTML = '';
  for (const f of files) {
    const li = document.createElement('li');
    const s = document.createElement('span');
    s.textContent = f;
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'ghost'; b.textContent = '复制路径';
    b.onclick = () => navigator.clipboard?.writeText(f);
    li.append(s, b);
    ul.appendChild(li);
  }
}

function fmtSeconds(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const pad = (n) => String(n).padStart(2, '0');
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

// ---------------------------------------------------------------- log
function appendLines(lines) {
  const log = $('log');
  const stick = $('autoscroll').checked;
  const frag = document.createDocumentFragment();
  for (const line of lines) {
    const div = document.createElement('div');
    if (line.includes(' - ERROR - ') || line.startsWith('[ERROR]')) div.className = 'lvl-error';
    else if (line.includes(' - WARNING - ') || line.startsWith('[WARNING]')) div.className = 'lvl-warn';
    else if (line.startsWith('[WEB]') || line.startsWith('[CANCELLED]')) div.className = 'lvl-web';
    div.textContent = line;
    frag.appendChild(div);
  }
  log.appendChild(frag);
  S.logCount += lines.length;
  while (S.logCount > 3000 && log.firstChild) { log.removeChild(log.firstChild); S.logCount--; }
  if (stick) log.scrollTop = log.scrollHeight;
}

// ---------------------------------------------------------------- events
function bind() {
  for (const el of document.querySelectorAll('input[name=flow]')) {
    el.onchange = () => { applyFlow(el.value); persistForm(); };
  }

  $('btnBrowse').onclick = () => openBrowser(null);
  $('btnClearInputs').onclick = () => { S.inputs = []; renderInputs(); persistForm(); };
  for (const b of document.querySelectorAll('[data-browse-dir]')) {
    b.onclick = () => openBrowser(b.dataset.browseDir);
  }

  $('btnCloseDlg').onclick = () => $('browser').close();
  $('btnUp').onclick = () => navigate($('browser').dataset.parent || '');
  $('driveSelect').onchange = (e) => navigate(e.target.value);
  $('pathInput').onkeydown = (e) => { if (e.key === 'Enter') navigate(e.target.value); };

  $('btnAddSelected').onclick = () => {
    if (S.browserTarget) {
      $(S.browserTarget).value = S.browserPath || '';
      persistForm();
    } else {
      addInputs([...S.browserSelection]);
    }
    $('browser').close();
  };
  $('btnAddFolder').onclick = async () => {
    if (!S.browserPath) return;
    const kind = S.flow === 'translate' ? 'srt' : 'media';
    const { files } = await api.get('/api/browse/expand?' +
      qp({ path: S.browserPath, kind }));
    addInputs(files.map((f) => f.path));
    $('browser').close();
  };

  $('btnStart').onclick = onStart;
  $('btnCancel').onclick = onCancel;
  $('x_provider').onchange = () => { updateKeyHint(); persistForm(); };
  $('x_model').addEventListener('input', updateKeyHint);
  $('btnOllamaRefresh').onclick = loadOllamaModels;

  $('btnCopyLog').onclick = () => navigator.clipboard?.writeText($('log').textContent);
  $('btnClearLog').onclick = () => { $('log').textContent = ''; S.logCount = 0; };

  for (const id of [...TRANSCRIBE_FIELDS, ...TRANSLATE_FIELDS]) {
    const el = $(id);
    if (el) el.addEventListener('change', persistForm);
  }
}

async function loadOllamaModels() {
  const url = str('x_ollama_url');
  try {
    const { models, error } = await api.get('/api/ollama/models?' + qp({ base_url: url }));
    const list = $('ollamaModels');
    list.innerHTML = '';
    for (const m of models || []) {
      const o = document.createElement('option');
      o.value = typeof m === 'string' ? m : (m.name || m.model || '');
      list.appendChild(o);
    }
    $('keyHint').textContent = error
      ? `Ollama: ${error}`
      : `Ollama 本地模型 ${(models || []).length} 个，点开模型输入框可选。`;
  } catch (err) {
    $('keyHint').textContent = 'Ollama: ' + err.message;
  }
}

init().catch((err) => {
  document.body.insertAdjacentHTML('afterbegin',
    `<p class="error" style="padding:16px">初始化失败：${err.message}</p>`);
});
