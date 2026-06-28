#!/usr/bin/env python3
"""
Simple HTTP server with Range request support for testing encrypted video blob
"""

import os
import re
from http.server import HTTPServer, SimpleHTTPRequestHandler
from functools import partial


class RangeRequestHandler(SimpleHTTPRequestHandler):
    """HTTP handler with Range request support"""

    def end_headers(self):
        # Add CORS headers for local testing
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Range')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def send_head(self):
        """Common code for GET and HEAD commands with Range support"""
        path = self.translate_path(self.path)

        if os.path.isdir(path):
            return super().send_head()

        try:
            f = open(path, 'rb')
        except OSError:
            self.send_error(404, "File not found")
            return None

        try:
            fs = os.fstat(f.fileno())
            file_len = fs.st_size

            # Check for Range header
            range_header = self.headers.get('Range')

            if range_header:
                # Parse Range header: bytes=start-end
                match = re.match(r'bytes=(\d+)-(\d+)', range_header)
                if match:
                    start = int(match.group(1))
                    end = int(match.group(2))

                    if start >= file_len or end >= file_len or start > end:
                        self.send_error(416, "Requested Range Not Satisfiable")
                        f.close()
                        return None

                    # Seek to start position
                    f.seek(start)

                    # Send partial content response
                    self.send_response(206, 'Partial Content')
                    self.send_header("Content-Type", self.guess_type(path))
                    self.send_header("Content-Range", f"bytes {start}-{end}/{file_len}")
                    self.send_header("Content-Length", str(end - start + 1))
                    self.send_header("Accept-Ranges", "bytes")
                    self.end_headers()

                    return f

            # No Range header - send full file
            self.send_response(200)
            self.send_header("Content-Type", self.guess_type(path))
            self.send_header("Content-Length", str(file_len))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            return f

        except Exception as e:
            f.close()
            raise

    def copyfile(self, source, outputfile):
        """Copy file with proper handling for Range requests"""
        # For Range requests, read only the requested bytes
        range_header = self.headers.get('Range')

        if range_header:
            match = re.match(r'bytes=(\d+)-(\d+)', range_header)
            if match:
                start = int(match.group(1))
                end = int(match.group(2))
                length = end - start + 1

                # Read and write in chunks
                bytes_left = length
                while bytes_left > 0:
                    chunk_size = min(8192, bytes_left)
                    chunk = source.read(chunk_size)
                    if not chunk:
                        break
                    outputfile.write(chunk)
                    bytes_left -= len(chunk)
                return

        # Default behavior for full file
        super().copyfile(source, outputfile)


def run_server(directory='.', port=8000):
    """Run HTTP server with Range support"""
    os.chdir(directory)

    handler = RangeRequestHandler
    server = HTTPServer(('', port), handler)

    print(f"=" * 60)
    print(f"HTTP Server with Range Request Support")
    print(f"=" * 60)
    print(f"Serving directory: {os.getcwd()}")
    print(f"Server running on: http://localhost:{port}")
    print(f"Open player at: http://localhost:{port}/player.html")
    print(f"=" * 60)
    print("Press Ctrl+C to stop")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\nServer stopped")


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='HTTP server with Range support')
    parser.add_argument('-d', '--directory', default='.', help='Directory to serve')
    parser.add_argument('-p', '--port', type=int, default=8000, help='Port number')

    args = parser.parse_args()

    run_server(args.directory, args.port)
