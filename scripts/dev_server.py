import http.server
import socketserver
import os

PORT = 8081
DIRECTORY = os.path.join(os.path.dirname(__file__), "baseera_mobile_app", "assets", "www")

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

if __name__ == "__main__":
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"Serving Baseera Mobile App at http://localhost:{PORT}/dashboard.html")
        httpd.serve_forever()
