'use strict';

const status = document.querySelector('#status');
const login = document.querySelector('#login');
const resources = document.querySelector('#resources');
const project = document.querySelector('#project');
const projectForm = document.querySelector('#project-form');
const connectionForm = document.querySelector('#connection-form');
const connections = document.querySelector('#connections');
let csrf;

async function api(path, method = 'GET', body) {
  const headers = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET' && csrf) headers['X-CSRF-Token'] = csrf;
  const response = await fetch(path, { method, headers, body: JSON.stringify(body), cache: 'no-store' });
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401) showLogin();
    throw new Error(result.message || result.error || `HTTP ${response.status}`);
  }
  return result;
}

function element(parent, tag, text) {
  const node = document.createElement(tag);
  node.textContent = text;
  parent.append(node);
  return node;
}

function showLogin() {
  csrf = undefined;
  connections.replaceChildren();
  resources.hidden = true;
  login.hidden = false;
}

async function list(path, filters = {}) {
  const items = [];
  const query = new URLSearchParams({ ...filters, limit: 100, offset: 0 });
  while (true) {
    const page = await api(`${path}?${query}`);
    items.push(...page.items);
    if (!page.items.length || items.length >= page.total) return items;
    query.set('offset', items.length);
  }
}

async function handle(control, work) {
  if (control.disabled) return;
  control.disabled = true;
  try {
    await work();
    status.textContent = '已连接本机资源库';
  } catch (error) {
    status.textContent = error.message;
  } finally {
    control.disabled = false;
  }
}

function submit(form, work) {
  form.addEventListener('submit', event => {
    event.preventDefault();
    handle(form.querySelector('button[type=submit]'), work);
  });
}

function action(parent, name, work) {
  const button = element(parent, 'button', name);
  button.type = 'button';
  button.addEventListener('click', () => handle(button, work));
}

function renderConnection(connection) {
  const card = element(connections, 'article', '');
  element(card, 'h2', connection.name);
  element(card, 'p', `${connection.kind} · ${connection.endpoint} · ${connection.model || 'HTTP Agent'}`);
  element(card, 'p', `连接 ID ${connection.id}`);
  element(card, 'p', `${connection.has_api_key ? '密钥已配置' : '未配置密钥'} · 凭据版本 ${connection.key_version}${connection.archived_at ? ' · 已归档' : ''}`);
  const form = element(card, 'form', '');
  form.method = 'post';
  const name = element(element(form, 'label', '连接名称'), 'input', '');
  name.value = connection.name;
  name.required = true;
  name.maxLength = 200;
  const key = element(element(form, 'label', '更换密钥'), 'input', '');
  key.type = 'password';
  key.autocomplete = 'off';
  element(form, 'button', '保存').type = 'submit';
  const update = async values => {
    await api(`/v1/model-connections/${connection.id}`, 'PATCH', values);
    await loadConnections();
  };
  submit(form, async () => {
    const values = { name: name.value };
    if (key.value) values.api_key = key.value;
    key.value = '';
    await update(values);
  });
  if (connection.has_api_key) action(form, '清除密钥', () => update({ api_key: null }));
  action(form, connection.archived_at ? '恢复连接' : '归档连接', () => update({ archived: !connection.archived_at }));
}

async function loadConnections() {
  const id = project.value;
  connections.replaceChildren();
  connectionForm.hidden = !id;
  connectionForm.elements.key.value = '';
  if (!id) return;
  const items = await list('/v1/model-connections', { project_id: id, archived: 'include' });
  if (project.value !== id || !csrf) return;
  connections.replaceChildren();
  for (const item of items) renderConnection(item);
}

async function loadProjects(selected = project.value) {
  const items = await list('/v1/projects');
  project.replaceChildren();
  for (const item of items) element(project, 'option', item.name).value = item.id;
  if (items.some(item => item.id === selected)) project.value = selected;
  await loadConnections();
}

async function showResources(session) {
  csrf = session.csrf_token;
  login.hidden = true;
  resources.hidden = false;
  await loadProjects();
}

submit(login, async () => {
  const { username, password } = login.elements;
  const body = { username: username.value, password: password.value };
  password.value = '';
  await showResources(await api('/v1/auth/session', 'POST', body));
});
submit(projectForm, async () => {
  const created = await api('/v1/projects', 'POST', { name: projectForm.elements.name.value });
  projectForm.reset();
  await loadProjects(created.id);
});
submit(connectionForm, async () => {
  const { name, kind, endpoint, model, key } = connectionForm.elements;
  const values = { project_id: project.value, name: name.value, kind: kind.value, endpoint: endpoint.value };
  if (model.value.trim()) values.model = model.value;
  if (key.value) values.api_key = key.value;
  key.value = '';
  await api('/v1/model-connections', 'POST', values);
  connectionForm.reset();
  await loadConnections();
});
project.addEventListener('change', () => handle(project, loadConnections));
document.querySelector('#logout').addEventListener('click', async () => {
  try {
    await api('/v1/auth/session', 'DELETE');
    showLogin();
    status.textContent = '已退出';
  } catch (error) {
    status.textContent = error.message;
  }
});

api('/v1/auth/me').then(showResources).then(() => {
  status.textContent = '已连接本机资源库';
}).catch(error => {
  showLogin();
  status.textContent = error.message;
});
