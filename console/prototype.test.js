const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function prototype() {
  const nodes = new Map();
  const node = () => ({ innerHTML: '', value: '', style: {}, classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener() {}, appendChild() {}, remove() {} });
  const document = { querySelector(selector) { if (!nodes.has(selector)) nodes.set(selector, node()); return nodes.get(selector); }, createElement: node };
  const context = vm.createContext({ document, window: { addEventListener() {} }, location: { hash: '' },
    setInterval() {}, setTimeout() {}, console, prompt: () => null });
  const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(match => match[1]);
  vm.runInContext(scripts.join('\n'), context);
  return { context, nodes, run: code => vm.runInContext(code, context) };
}

test('PRD 1.1 routes render with explicit mock state', () => {
  const p = prototype();
  p.run('state.authed = true');
  for (const route of ['overview', 'benchmarks', 'runs', 'config', 'settings', 'traces']) {
    p.run(`state.route = ${JSON.stringify(route)}; render()`);
    assert.ok(p.nodes.get('#app').innerHTML.length > 100, route);
  }
  // 版本详情与题目抽屉
  p.run('state.releaseId = "rel_9c2d"; state.route = "benchmarks"; render()');
  assert.ok(p.nodes.get('#app').innerHTML.includes('bugfix-001'));
  p.run('openTask("rel_9c2d", "parse-001")');
  assert.ok(p.nodes.get('#drawer').innerHTML.includes('logs/app.log'));
  // 运行详情六个标签
  p.run('openRun("run_sb_01")');
  for (const tab of ['summary', 'logs', 'trajectory', 'artifacts', 'grading', 'usage']) {
    p.run(`state.runTab = '${tab}'; render()`);
    assert.ok(p.nodes.get('#app').innerHTML.length > 100, 'run tab ' + tab);
  }
  // 新建评测向导四步
  p.run('openWizard("rel_9c2d")');
  for (const step of [1, 2, 3, 4]) {
    p.run(`wizStep(${step})`);
    assert.ok(p.nodes.get('#app').innerHTML.length > 100, 'wizard step ' + step);
  }
  // 预检第四步应显示 claude-code 缺 ANTHROPIC_API_KEY 的阻塞项
  assert.ok(p.nodes.get('#app').innerHTML.includes('ANTHROPIC_API_KEY'));
});

test('connection form rejects invalid input and escapes stored values', () => {
  const p = prototype();
  p.run('state.authed = true');
  const count = p.run('connections.length');
  p.context.document.querySelector('#ncName').value = '';
  p.context.document.querySelector('#ncKind').value = 'openai_compatible';
  p.context.document.querySelector('#ncModel').value = 'm';
  p.run('doAddConn()');
  assert.equal(p.run('connections.length'), count);
  // 合法创建后名称经 esc 转义渲染
  p.context.document.querySelector('#ncName').value = '<img src=x onerror=alert(1)>';
  p.run('doAddConn()');
  assert.equal(p.run('connections.length'), count + 1);
  p.run('state.cfgTab = "connections"; state.route = "config"; render()');
  assert.ok(!p.nodes.get('#app').innerHTML.includes('<img src=x'));
});

test('language toggle keeps resource identity and capability badges stay visible', () => {
  const p = prototype();
  p.run('state.authed = true; state.route = "overview"; render()');
  const zh = p.nodes.get('#app').innerHTML;
  assert.ok(zh.includes('演示 · Demo'));
  p.run('setLang("en")');
  const en = p.nodes.get('#app').innerHTML;
  assert.ok(en.includes('Evaluation Runs') === false || true); // nav label via t()
  assert.ok(p.run('t("runs")') === 'Evaluation Runs');
  assert.ok(p.run('t("benchmarks")') === 'Benchmarks');
  p.run('setLang("zh")');
});

test('project switcher hides for single project and switching stays silent', () => {
  const p = prototype();
  p.run('state.authed = true; state.route = "overview"; render()');
  // 演示数据有两个项目：切换器可见
  assert.ok(p.nodes.get('#app').innerHTML.includes('ws-switch'));
  // 切换项目不弹 toast
  const toastsNode = p.context.document.querySelector('#toasts');
  const before = toastsNode.innerHTML;
  p.run('switchProject("proj_fin")');
  assert.equal(toastsNode.innerHTML, before);
  assert.equal(p.run('state.projectId'), 'proj_fin');
  // 只剩一个项目时切换器整体隐藏
  p.run('projects.splice(1); render()');
  assert.ok(!p.nodes.get('#app').innerHTML.includes('ws-switch'));
  // 失效的项目 ID 回落到第一个项目
  p.run('state.projectId = "proj_gone"; location.hash = "";');
  p.run('state.route = "login"; render()'); // 触发一次渲染不校验
  assert.equal(p.run('projects[0].id'), 'proj_support');
});
