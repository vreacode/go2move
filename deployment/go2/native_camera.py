#!/usr/bin/env python3
"""Read Go2's native front camera through official SDK2 VideoClient.

Run with matching DDS libraries, independently of ROS:
  source /home/unitree/go2move/deployment/go2/source_official_go2_sdk.sh
  /home/unitree/go2_sdk2_venv/bin/python native_camera.py --http-port 8766

The output directory contains the original latest.jpg and status.json.
stdout is newline-delimited JSON suitable for a parent supervisor.
Only the video image-sample API is called; no sport/posture APIs are used.
"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import threading
import time

DEFAULT_CONFIG = Path(__file__).with_name('cyclonedds_go2.xml')
DEFAULT_OUTPUT = Path(__file__).with_name('runtime') / 'native_camera'


def atomic_write(path, payload):
    temporary = path.with_name('.' + path.name + '.' + str(os.getpid()) + '.tmp')
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def serve_http(output_dir, port, fps, stop):
    """Serve the same files on loopback for the authenticated dashboard proxy."""
    def current_jpeg():
        metadata = json.loads((output_dir / 'status.json').read_text())
        captured_at = float(metadata.get('captured_at', 0))
        age = time.time() - captured_at
        if not metadata.get('running') or not -0.25 <= age <= 2.0:
            raise FileNotFoundError('Camera frame is stale or camera worker stopped')
        return (output_dir / 'latest.jpg').read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            path = self.path.split('?', 1)[0]
            if path == '/stream.mjpg':
                try:
                    current_jpeg()
                except (OSError, ValueError, TypeError):
                    self.send_error(503, 'Camera frame unavailable or stale')
                    return
                self.connection.settimeout(5)
                self.send_response(200)
                self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                try:
                    while not stop.is_set():
                        jpeg = current_jpeg()
                        header = b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + str(len(jpeg)).encode() + b'\r\n\r\n'
                        self.wfile.write(header + jpeg + b'\r\n')
                        self.wfile.flush()
                        stop.wait(1 / fps)
                except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError, ValueError, TypeError):
                    pass
                return
            routes = {'/snapshot.jpg': ('latest.jpg', 'image/jpeg'),
                      '/status': ('status.json', 'application/json')}
            if path not in routes:
                self.send_error(404)
                return
            filename, content_type = routes[path]
            try:
                payload = current_jpeg() if path == '/snapshot.jpg' else (output_dir / filename).read_bytes()
            except (OSError, ValueError, TypeError):
                self.send_error(503, 'Camera frame unavailable or stale')
                return
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name='native-camera-http', daemon=True).start()
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--dds-config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--fps', type=float, default=5.0)
    parser.add_argument('--http-port', type=int, default=0, help='Optional loopback HTTP/MJPEG port; 0 disables')
    parser.add_argument('--duration', type=float, default=0.0, help='Seconds, 0 runs until stopped')
    parser.add_argument('--frames', type=int, default=0, help='Successful captures, 0 has no frame limit')
    parser.add_argument('--timeout', type=float, default=2.0)
    args = parser.parse_args()
    if not 0 < args.fps <= 30 or args.duration < 0 or args.frames < 0 or args.timeout <= 0 or not 0 <= args.http_port <= 65535:
        parser.error('fps must be in (0, 30], duration/frames nonnegative, timeout positive')

    import cv2
    import numpy as np
    from unitree_sdk2py.core import channel
    from unitree_sdk2py.go2.video.video_client import VideoClient

    channel.ChannelConfigAutoDetermine = args.dds_config.read_text()
    channel.ChannelFactoryInitialize(0)
    client = VideoClient()
    client.SetTimeout(args.timeout)
    client.Init()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    started = time.monotonic()
    first_frame = None
    last_frame = None
    previous_digest = None
    last_report = started - 2
    status = {
        'source': 'unitree_sdk2_video', 'running': True,
        'image_path': str((args.output_dir / 'latest.jpg').resolve()),
        'frames': 0, 'distinct_frames': 0, 'errors': 0,
        'target_fps': args.fps, 'width': None, 'height': None,
    }

    def publish(report=False):
        elapsed = time.monotonic() - started
        frame_elapsed = last_frame - first_frame if first_frame is not None else 0
        status.update(elapsed_seconds=round(elapsed, 3),
                      fps=round((status['frames'] - 1) / frame_elapsed, 2) if frame_elapsed else 0,
                      distinct_fps=round(max(0, status['distinct_frames'] - 1) / frame_elapsed, 2) if frame_elapsed else 0)
        payload = json.dumps(status, ensure_ascii=False)
        atomic_write(args.output_dir / 'status.json', (payload + '\n').encode())
        if report:
            print(payload, flush=True)

    if args.http_port:
        status['http_base'] = 'http://127.0.0.1:' + str(args.http_port)
    publish(True)
    server = serve_http(args.output_dir, args.http_port, args.fps, stop) if args.http_port else None
    try:
        while not stop.is_set():
            cycle = time.monotonic()
            if args.duration and cycle - started >= args.duration:
                break
            code, data = client.GetImageSample()
            status['last_code'] = int(code)
            if code == 0:
                jpeg = bytes(data)
                image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                if image is None or not jpeg.startswith(b'\xff\xd8'):
                    status['errors'] += 1
                    status['last_error'] = 'Image sample could not be decoded as JPEG'
                else:
                    last_frame = time.monotonic()
                    first_frame = first_frame or last_frame
                    digest = hashlib.sha256(jpeg).hexdigest()
                    status['distinct_frames'] += int(digest != previous_digest)
                    previous_digest = digest
                    atomic_write(args.output_dir / 'latest.jpg', jpeg)
                    status.update(frames=status['frames'] + 1, width=int(image.shape[1]),
                                  height=int(image.shape[0]), bytes=len(jpeg),
                                  captured_at=time.time(), sha256=digest)
                    status.pop('last_error', None)
            else:
                status['errors'] += 1
                status['last_error'] = 'GetImageSample returned code ' + str(code)
            now = time.monotonic()
            report = now - last_report >= 2
            publish(report)
            if report:
                last_report = now
            if args.frames and status['frames'] >= args.frames:
                break
            stop.wait(max(0, 1 / args.fps - (now - cycle)))
    finally:
        stop.set()
        if server:
            server.shutdown()
            server.server_close()
        status['running'] = False
        publish(True)
    return 0 if status['frames'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
