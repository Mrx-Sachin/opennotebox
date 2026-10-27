"""Exercise the SSRF guarded transport against an actual local HTTP socket."""
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from app.routers.documents import _pinned_client


def test_pinned_transport_uses_the_validated_ip_for_http_requests():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"pinned response"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    client = _pinned_client({f"localhost:{port}": ("127.0.0.1",)})
    try:
        response = client.get(f"http://localhost:{port}/file.pdf")
        assert response.status_code == 200
        assert response.content == b"pinned response"
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
