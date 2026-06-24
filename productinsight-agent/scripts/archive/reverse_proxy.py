#!/usr/bin/env python3
"""
Reverse proxy: merges frontend + backend on single port.
- /api/*, /docs/*, /openapi/* -> backend :8005
- everything else -> frontend :8502
"""
import asyncio
import httpx

PROXY_PORT = 9000
BACKEND_HOST = "localhost"
BACKEND_PORT = 8005
FRONTEND_HOST = "localhost"
FRONTEND_PORT = 8502

BACKEND_URL = f"http://{BACKEND_HOST}:{BACKEND_PORT}"
FRONTEND_URL = f"http://{FRONTEND_HOST}:{FRONTEND_PORT}"

BACKEND_PATHS = {"/api", "/docs", "/openapi"}

async def proxy_request(reader, writer):
    try:
        # Read full HTTP request
        request_data = await asyncio.wait_for(reader.read(65536), timeout=10)
        if not request_data:
            writer.close()
            return

        # Parse request line
        text = request_data.decode('utf-8', errors='surrogateescape')
        header_end = text.find('\r\n\r\n')
        if header_end == -1:
            writer.close()
            return

        header_text = text[:header_end]
        headers = {}
        request_line = header_text.split('\r\n')[0]
        parts = request_line.split(' ')
        if len(parts) < 2:
            writer.close()
            return
        method, raw_path = parts[0], parts[1]
        for line in header_text.split('\r\n')[1:]:
            if ':' not in line:
                continue
            k, v = line.split(':', 1)
            headers[k.strip().lower()] = v.strip()

        # Remove hop-by-hop headers
        skip_headers = {
            'host', 'connection', 'keep-alive', 'proxy-connection',
            'transfer-encoding', 'upgrade'
        }
        filtered = {k: v for k, v in headers.items() if k not in skip_headers}

        # Determine target
        path_prefix = raw_path.split('?')[0]
        if any(path_prefix.startswith(p) for p in BACKEND_PATHS):
            target_base = BACKEND_URL
        else:
            target_base = FRONTEND_URL

        target_url = target_base + raw_path

        body = None
        if header_end + 4 < len(text):
            body = text[header_end + 4:].encode('utf-8')
        elif 'content-length' in headers and int(headers.get('content-length', 0)) > 0:
            # Read remaining body
            remaining = await asyncio.wait_for(
                reader.read(int(headers['content-length'])), timeout=10
            )
            body = remaining

        # Forward request
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.request(
                method=method,
                url=target_url,
                headers=filtered,
                content=body,
                follow_redirects=False,
            )

        # Build response
        status_line = f"HTTP/1.1 {resp.status_code} {resp.reason_phrase}\r\n"
        out_headers = [status_line]
        for k, v in resp.headers.items():
            if k.lower() not in skip_headers:
                out_headers.append(f"{k}: {v}\r\n")
        out_headers.append("\r\n")
        writer.write(''.join(out_headers).encode())
        await writer.drain()

        # Stream body
        await resp.aclose()
        writer.write(resp.content)
        await writer.drain()

    except Exception as e:
        print(f"Proxy error: {e}", flush=True)
    finally:
        writer.close()

async def main():
    server = await asyncio.start_server(proxy_request, '0.0.0.0', PROXY_PORT)
    print(f"Reverse proxy: http://0.0.0.0:{PROXY_PORT}")
    print(f"  /api/*, /docs/* -> backend   :{BACKEND_PORT}")
    print(f"  /*              -> frontend :{FRONTEND_PORT}")
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
