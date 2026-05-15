import os, http.server, socketserver

PORT = int(os.environ.get("PORT", 8080))

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"status":"ok","service":"tz-signal-engine"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass  # suppress access logs

with socketserver.TCPServer(("", PORT), Handler) as httpd:
    print(f"Listening on port {PORT}")
    httpd.serve_forever()
