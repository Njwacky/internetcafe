"""Local preview server that mimics the Vercel config (rewrites + headers) in this repo.

Serves the repo root so /, /get, /app show the download page and /dashboard, /admin
show the owner console — exactly like production, without deploying.

Run:  python3 tools/preview_site.py [port]
"""
import http.server
import json
import os
import socketserver
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, 'vercel.json'), encoding='utf-8') as fh:
    CFG = json.load(fh)
REWRITES = {r['source']: r['destination'] for r in CFG.get('rewrites', [])}
ROUTES = {
    '/owner-dashboard.html': {'X-Frame-Options': 'DENY', 'X-Robots-Tag': 'noindex, nofollow',
                              'Cache-Control': 'private, no-store, max-age=0'},
    '/sw.js': {'Cache-Control': 'public, max-age=0, must-revalidate', 'Service-Worker-Allowed': '/'},
    '/manifest.webmanifest': {'Content-Type': 'application/manifest+json'},
}
GLOBAL = {h['key']: h['value'] for h in CFG['headers'][0]['headers']}


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def translate_path(self, path):
        clean = path.split('?', 1)[0].split('#', 1)[0]
        clean = REWRITES.get(clean, clean)
        return super().translate_path(clean)

    def end_headers(self):
        clean = self.path.split('?', 1)[0]
        for key, val in GLOBAL.items():
            self.send_header(key, val)
        for key, val in ROUTES.get(REWRITES.get(clean, clean), {}).items():
            self.send_header(key, val)
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write('%s - %s\n' % (self.address_string(), fmt % args))


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(('0.0.0.0', port), Handler) as httpd:
        print(f'Serving {ROOT} on http://0.0.0.0:{port}  (/, /get, /app -> download; /dashboard, /admin -> console)')
        httpd.serve_forever()
