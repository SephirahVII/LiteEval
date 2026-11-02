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
