'use strict';

const form = document.getElementById('connect');
const status = document.getElementById('status');
const container = document.getElementById('turns');
const turns = new Map();
let connection;

function add(parent, tag, text) {
  const element = document.createElement(tag);
  element.textContent = text;
  parent.appendChild(element);
  return element;
}

function renderEvent(event) {
  const data = event.payload;
  if ((!event.kind.startsWith('dialogue_') && event.kind !== 'log') || !Number.isInteger(data.turn_index)) return;
  const key = `${data.rollout_id}:${data.turn_index}`;
  let turn = turns.get(key);
  if (!turn) {
    const card = add(container, 'article', '');
    add(card, 'h2', `${data.task_id} · Turn ${data.turn_index}`);
    turn = { card, state: add(card, 'p', '进行中 / Running') };
    turns.set(key, turn);
  }
  const append = (label, text) => {
    add(turn.card, 'p', label);
    add(turn.card, 'pre', typeof text === 'string' ? text : JSON.stringify(text, null, 2));
  };
  switch (event.kind) {
    case 'dialogue_turn_started':
      add(turn.card, 'time', event.created_at);
      append('本轮指令 / Instruction', data.instruction);
      break;
    case 'dialogue_session':
      add(turn.card, 'p', `Session ${data.session_id}`);
      break;
    case 'dialogue_assistant':
      append('Agent', data.content);
      turn.lastReply = data.content;
      break;
    case 'dialogue_tool_call':
    case 'dialogue_tool_result': {
      const details = add(turn.card, 'details', '');
      add(details, 'summary', event.kind === 'dialogue_tool_call' ? `Tool · ${data.content.name}` : 'Tool result');
      add(details, 'pre', JSON.stringify(data.content, null, 2));
      break;
    }
    case 'log': {
      const details = add(turn.card, 'details', '');
      add(details, 'summary', `Log · ${data.stream}`);
      add(details, 'pre', data.text);
      break;
    }
    case 'dialogue_turn_completed': {
      turn.state.textContent = `已结束 / Completed · ${data.usage.latency_ms} ms`;
      if (turn.lastReply !== data.output) append('本轮结果 / Turn result', data.output);
      const counters = data.usage.normalized;
      add(turn.card, 'p', `Input ${counters.input_tokens ?? '—'} · Output ${counters.output_tokens ?? '—'} · Cache ${counters.cached_input_tokens ?? '—'} · Reasoning ${counters.reasoning_tokens ?? '—'}`);
      break;
    }
    case 'dialogue_turn_failed':
    case 'dialogue_turn_interrupted':
      turn.state.textContent = data.timed_out ? '超时 / Timed out' : '已中止 / Stopped';
      append('原因 / Reason', data.reason);
      break;
  }
}

async function poll(current) {
  const query = new URLSearchParams({ after: current.after });
  if (current.project) query.set('project_id', current.project);
  try {
    const response = await fetch(`/v1/benchmark-executions/${current.id}/events?${query}`, {
      headers: current.key ? { Authorization: `Bearer ${current.key}` } : {}, signal: current.controller.signal,
      cache: 'no-store'
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const page = await response.json();
    if (connection !== current) return;
    for (const event of page.events) renderEvent(event);
    current.after = page.next_after;
    status.textContent = `Execution ${page.status} · Cursor ${current.after}${turns.size ? '' : ' · 尚无对话事件'}`;
    if (page.has_more || page.status === 'running') {
      current.timer = setTimeout(() => poll(current), page.has_more ? 0 : 1200);
    }
  } catch (error) {
    if (connection === current && !current.controller.signal.aborted) {
      status.textContent = `连接已停止 / Connection stopped · ${error.message}`;
    }
  }
}

form.addEventListener('submit', event => {
  event.preventDefault();
  if (connection) {
    connection.controller.abort();
    clearTimeout(connection.timer);
  }
  connection = { id: form.elements.execution.value, key: form.elements.key.value,
    project: form.elements.project.value, after: 0, controller: new AbortController() };
  form.elements.key.value = '';
  turns.clear();
  container.replaceChildren();
  status.textContent = '正在读取 / Loading';
  poll(connection);
});
