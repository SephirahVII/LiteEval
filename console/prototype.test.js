const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function prototype() {
  const nodes = new Map();
  const node = () => ({ innerHTML: '', value: '', style: {}, classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {}, appendChild() {}, remove() {} });
  const document = { querySelector(selector) { if (!nodes.has(selector)) nodes.set(selector, node()); return nodes.get(selector); }, createElement: node };
  const context = vm.createContext({ document, window: { addEventListener() {} }, location: { hash: '' },
    setInterval() {}, setTimeout() {}, console });
  const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(match => match[1]);
  vm.runInContext(scripts.join('\n'), context);
  return { context, nodes, run: code => vm.runInContext(code, context) };
}

test('prototype routes and run tabs render with explicit mock state', () => {
  const p = prototype();
  p.run('state.authed = true');
  for (const route of ['overview', 'experiments', 'compare', 'datasets', 'harnesses', 'evaluators', 'connections', 'tools', 'keys', 'traces']) {
    p.run(`state.route = ${JSON.stringify(route)}; render()`);
    assert.ok(p.nodes.get('#app').innerHTML.length > 100, route);
  }
  p.run('openDetail(experiments[0].id); openRun(caseResults[0].id)');
  for (const tab of ['logs', 'trajectory', 'artifacts', 'usage']) p.run(`state.runTab = '${tab}'; render()`);
  const widths = [...p.nodes.get('#app').innerHTML.matchAll(/<i style="width:([\d.]+)%;/g)].map(match => Number(match[1]));
  assert.equal(widths.length, 4);
  assert.ok(Math.abs(widths.reduce((a, b) => a + b, 0) - 100) < 0.3);
  const keyCount = p.run('userKeys.length');
  p.nodes.get('#nkName') || p.context.document.querySelector('#nkName');
  p.nodes.get('#nkName').value = '<img src=x onerror=alert(1)>';
  p.run('doAddKey()');
  assert.equal(p.run('userKeys.length'), keyCount);
});
