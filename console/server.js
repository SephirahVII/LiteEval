// LiteEval 控制台原型 —— 零依赖静态服务器
// 支持透传 --port / --host 参数，默认 7100 / 127.0.0.1
const http = require("http");
const fs = require("fs");
const path = require("path");

function arg(name, fallback) {
  const i = process.argv.indexOf(name);
  if (i !== -1 && process.argv[i + 1]) return process.argv[i + 1];
  const eq = process.argv.find((a) => a.startsWith(name + "="));
  if (eq) return eq.split("=")[1];
  return fallback;
}

const port = Number(arg("--port", process.env.PORT || 7100));
const host = arg("--host", process.env.HOST || "127.0.0.1");
const root = __dirname;

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
};

function createServer(directory = root) {
  directory = path.resolve(directory);
  return http.createServer((req, res) => {
    if (!['GET', 'HEAD'].includes(req.method)) {
      res.writeHead(405, { Allow: 'GET, HEAD' });
      return res.end('Method Not Allowed');
    }
    let urlPath;
    try {
      urlPath = decodeURIComponent(req.url.split('?')[0]);
      if (!urlPath.startsWith('/') || urlPath.includes('\0')) throw new Error('Invalid path');
    } catch {
      res.writeHead(400);
      return res.end('Bad Request');
    }
    if (urlPath === "/") urlPath = "/index.html";
    const file = path.resolve(directory, '.' + urlPath);
    const relative = path.relative(directory, file);
    if (relative === '..' || relative.startsWith('..' + path.sep) || path.isAbsolute(relative)) {
      res.writeHead(403);
      return res.end("Forbidden");
    }
    fs.readFile(file, (err, data) => {
      if (err) {
        res.writeHead(404);
        return res.end("Not Found");
      }
      res.writeHead(200, { "Content-Type": MIME[path.extname(file)] || "application/octet-stream" });
      res.end(req.method === 'HEAD' ? undefined : data);
    });
  });
}

if (require.main === module) {
  const server = createServer();
  server.listen(port, host, () => {
    console.log(`LiteEval console prototype → http://${host}:${server.address().port}/`);
  });
}
module.exports = { createServer };
