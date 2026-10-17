#!/usr/bin/env python
# PiCamera2 streaming video example
# We are using it to help focus the optical camera
# Open http://<the Pi's Tailscale IP or name>:8080/ in a browser.
#
# It listens on the Pi's Tailscale address only (`tailscale ip -4`), else on
# 127.0.0.1 (reach it with `ssh -L 8080:127.0.0.1:8080`), never on every
# interface: the stream is unauthenticated. --bind ADDRESS overrides that
# (--bind 0.0.0.0 for a bench on a trusted network); --port changes the port.

import argparse
import io
import socketserver
import subprocess
from http import server
from threading import Condition

# HTML page for the MJPEG streaming demo
PAGE = """\
<html>
<head>
<title>Pi Camera Stream</title>
</head>
<body>
<img src="stream.mjpg" width="640" height="480" />
</body>
</html>
"""

# Class to handle streaming output
class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()

# Class to handle HTTP requests
class StreamingHandler(server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            # Redirect root path to index.html
            self.send_response(301)
            self.send_header('Location', '/index.html')
            self.end_headers()
        elif self.path == '/index.html':
            # Serve the HTML page
            content = PAGE.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', len(content))
            self.end_headers()
            self.wfile.write(content)
        elif self.path == '/stream.mjpg':
            # Set up MJPEG streaming
            self.send_response(200)
            self.send_header('Age', 0)
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
            self.end_headers()
            try:
                while True:
                    with output.condition:
                        output.condition.wait()
                        frame = output.frame
                    self.wfile.write(b'--FRAME\r\n')
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Content-Length', len(frame))
                    self.end_headers()
                    self.wfile.write(frame)
                    self.wfile.write(b'\r\n')
            except Exception as e:
                print(
                    'Removed streaming client %s: %s',
                    self.client_address, str(e))
        else:
            # Handle 404 Not Found
            self.send_error(404)
            self.end_headers()

# Class to handle streaming server 
class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

def tailscale_ipv4():
    """The node's Tailscale IPv4 address, or None."""
    try:
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = out.stdout.split() if out.returncode == 0 else []
    return lines[0] if lines else None


def bind_address(override=None):
    """--bind if given, else the Tailscale address, else loopback."""
    return override or tailscale_ipv4() or "127.0.0.1"


output = None


def main(argv=None):
    global output
    parser = argparse.ArgumentParser(description="MJPEG stream from the optical camera, for focusing")
    parser.add_argument("--bind", help="address to listen on (default: Tailscale IPv4, else 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)

    from picamera2 import Picamera2
    from picamera2.encoders import JpegEncoder
    from picamera2.outputs import FileOutput

    # Create Picamera2 instance and configure it
    picam2 = Picamera2()
    picam2.configure(picam2.create_video_configuration(main={"size": (640, 480)}))
    output = StreamingOutput()
    picam2.start_recording(JpegEncoder(), FileOutput(output))

    try:
        # Set up and start the streaming server
        address = (bind_address(args.bind), args.port)
        print(f"Streaming on http://{address[0]}:{address[1]}/")
        httpd = StreamingServer(address, StreamingHandler)
        httpd.serve_forever()
    finally:
        # Stop recording when the script is interrupted
        picam2.stop_recording()


if __name__ == "__main__":
    main()

