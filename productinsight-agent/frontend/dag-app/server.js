import http from 'http';
import express from 'express';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DIST_DIR = path.join(__dirname, 'dist');
const PORT = 3001;
const BACKEND_HOST = '127.0.0.1';
const BACKEND_PORT = 8005;

const app = express();

// ── Native HTTP proxy for /api/* ───────────────────────────────────
// http-proxy-middleware v4 pathRewrite strips the mount-point path,
// but our FastAPI needs /api/runs/... unchanged. Manually proxy instead.
app.use('/api', (req, res) => {
  const options = {
    hostname: BACKEND_HOST,
    port: BACKEND_PORT,
    path: req.originalUrl,
    method: req.method,
    headers: {
      ...req.headers,
      host: `${BACKEND_HOST}:${BACKEND_PORT}`,
      'x-forwarded-for': req.ip,
      'x-forwarded-proto': 'http',
    },
  };

  const proxyReq = http.request(options, (proxyRes) => {
    // Forward status and headers
    res.writeHead(proxyRes.statusCode, proxyRes.headers);
    // Stream response body back to client
    proxyRes.pipe(res);
  });

  proxyReq.on('error', (err) => {
    console.error('[proxy] Error:', err.message);
    if (!res.headersSent) {
      res.status(502).json({ error: 'Backend unreachable', detail: err.message });
    }
  });

  // Stream request body to backend
  req.pipe(proxyReq);
});

// ── Static files (SPA) ─────────────────────────────────────────────
app.use(express.static(DIST_DIR, { index: 'index.html' }));

// Fallback: any unmatched route → index.html (SPA routing)
app.use((req, res) => {
  res.sendFile(path.join(DIST_DIR, 'index.html'));
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`[DAG App] Production server at http://0.0.0.0:${PORT}`);
  console.log(`[DAG App] Proxying /api/* → ${BACKEND_HOST}:${BACKEND_PORT}`);
  console.log(`[DAG App] Static build: ${DIST_DIR}`);
});
