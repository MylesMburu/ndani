"""Stand-in service for the e2e isolation test: listens on a port like the real one would.

The e2e test is about the NETWORK (who can reach whom), so the services only need to
accept TCP. The gateway stand-in also mimics /healthz and the 401 on missing keys.
"""
import http.server
import sys

port = int(sys.argv[1])


class Handler(http.server.BaseHTTPRequestHandler):
    def _reply(self):
        code = 200 if self.path == "/healthz" else 401
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    do_GET = do_POST = _reply

    def log_message(self, *args):
        pass


http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
