const { test } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { createServer } = require('./server');

test('serves files; rejects sibling traversal and malformed URLs without crashing', async () => {
  const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'liteeval-console-'));
  const root = path.join(temporary, 'console');
  await fs.mkdir(root);
  await fs.mkdir(path.join(temporary, 'console-private'));
  await fs.writeFile(path.join(root, 'index.html'), 'prototype');
  await fs.writeFile(path.join(temporary, 'console-private', 'secret.txt'), 'outside');
  const server = createServer(root);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  function request(urlPath, method = 'GET') {
    return new Promise((resolve, reject) => {
      http.request({ hostname: '127.0.0.1', port: server.address().port, path: urlPath, method }, res => {
        let body = ''; res.on('data', chunk => body += chunk);
        res.on('end', () => resolve({ status: res.statusCode, body }));
      }).on('error', reject).end();
    });
  }
  try {
    assert.deepEqual(await request('/'), { status: 200, body: 'prototype' });
    assert.equal((await request('/%2e%2e/console-private/secret.txt')).status, 403);
    assert.equal((await request('/%E0%A4%A')).status, 400);
    assert.equal((await request('/%00')).status, 400);
    assert.equal((await request('/missing')).status, 404);
    assert.equal((await request('/', 'POST')).status, 405);
    assert.deepEqual(await request('/', 'HEAD'), { status: 200, body: '' });
    assert.equal((await request('/')).status, 200);
  } finally {
    await new Promise(resolve => server.close(resolve));
    assert.equal(path.dirname(path.resolve(temporary)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(temporary).startsWith('liteeval-console-'));
    await fs.rm(temporary, { recursive: true, force: true });
  }
});

test('proxies Native events and Owner resource routes to the loopback API', async () => {
  const received = [];
  const upstream = http.createServer(async (req, res) => {
    let body = '';
    for await (const chunk of req) body += chunk;
    const { authorization, cookie, origin, 'x-csrf-token': csrf } = req.headers;
    received.push({ path: req.url, method: req.method, authorization, cookie, origin, csrf, body });
    res.writeHead(200, { 'Content-Type': 'application/json', 'Set-Cookie': 'owner=test; HttpOnly' });
    res.end('{"events":[],"next_after":12}');
  });
  await new Promise(resolve => upstream.listen(0, '127.0.0.1', resolve));
  const consoleServer = createServer(undefined, upstream.address().port);
  await new Promise(resolve => consoleServer.listen(0, '127.0.0.1', resolve));
  try {
    const origin = `http://127.0.0.1:${consoleServer.address().port}`;
    const route = `/v1/benchmark-executions/${'a'.repeat(32)}/events?after=12`;
    const response = await fetch(origin + route, { headers: { Authorization: 'Bearer test-project-key' } });
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.equal((await response.json()).next_after, 12);
    const resource = '/v1/model-connections/' + 'b'.repeat(32);
    const body = JSON.stringify({ api_key: 'fixture' });
    const patched = await fetch(origin + resource, { method: 'PATCH',
      headers: { Cookie: 'owner=test', Origin: origin, 'X-CSRF-Token': 'fixture-csrf',
        'Content-Type': 'application/json' }, body });
    assert.equal(patched.status, 200);
    assert.equal(patched.headers.get('set-cookie'), 'owner=test; HttpOnly');
    assert.equal((await fetch(origin + '/v1/experiments')).status, 404);
    assert.equal(received.length, 2);
    assert.equal(received[0].path, route);
    assert.equal(received[0].authorization, 'Bearer test-project-key');
    assert.deepEqual(received[1], { path: resource, method: 'PATCH', authorization: undefined,
      cookie: 'owner=test', origin, csrf: 'fixture-csrf', body });
  } finally {
    await new Promise(resolve => consoleServer.close(resolve));
    await new Promise(resolve => upstream.close(resolve));
  }
});
