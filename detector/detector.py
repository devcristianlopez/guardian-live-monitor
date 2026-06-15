"""
Motion detector for Guardian Live Monitor.

Captures video from a webcam or video file, detects motion via frame
differencing, and sends events to the backend API.
"""

import asyncio
import os
import socketserver
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

import cv2
import httpx
import numpy as np  # noqa: F401  # required by OpenCV internals

# ---------------------------------------------------------------------------
# Configuration from environment variables
# ---------------------------------------------------------------------------
CAMERA_ID = os.getenv("CAMERA_ID", "CAM-01")
SOURCE = os.getenv("SOURCE", "video")              # "webcam" o "video"
VIDEO_PATH = os.getenv("VIDEO_PATH", "/app/test_video.mp4")
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")
MOTION_THRESHOLD = int(os.getenv("MOTION_THRESHOLD", "5000"))
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "3"))

# ---------------------------------------------------------------------------
# MJPEG stream server (background thread)
# ---------------------------------------------------------------------------

STREAM_PORT = int(os.getenv("STREAM_PORT", "8080"))

_latest_jpeg: bytes | None = None
_jpeg_lock = threading.Lock()

# Pre-encode a black placeholder frame so the stream never starts empty
_placeholder = np.zeros((480, 640, 3), dtype=np.uint8)
_, _buf = cv2.imencode(".jpg", _placeholder, [cv2.IMWRITE_JPEG_QUALITY, 50])
_latest_jpeg = _buf.tobytes()


class _MJPEGHandler(BaseHTTPRequestHandler):
    """Serve latest frame as MJPEG stream on GET /stream."""

    def do_GET(self) -> None:
        if self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            while True:
                with _jpeg_lock:
                    jpeg = _latest_jpeg
                if jpeg:
                    try:
                        self.wfile.write(b"--frame\r\n")
                        self.wfile.write(b"Content-Type: image/jpeg\r\n")
                        self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode())
                        self.wfile.write(jpeg)
                        self.wfile.write(b"\r\n")
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        break
                time.sleep(0.05)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt: str, *args) -> None:
        pass  # suppress HTTP log spam


class _ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    """Threaded HTTP server for handling multiple stream clients."""
    allow_reuse_address = True
    daemon_threads = True


def _start_mjpeg_server() -> None:
    server = _ThreadingHTTPServer(("0.0.0.0", STREAM_PORT), _MJPEGHandler)
    print(f"[stream] MJPEG server listening on http://0.0.0.0:{STREAM_PORT}/stream")
    server.serve_forever()


# ---------------------------------------------------------------------------
# Synthetic test video generator
# ---------------------------------------------------------------------------


def _generate_test_video(path: str, duration_sec: int = 10) -> None:
    """Create a synthetic test video with periodic motion for demo purposes."""
    print(f"Generating synthetic test video: {path}")
    width, height, fps = 640, 480, 20
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(path, fourcc, fps, (width, height))

    for frame_idx in range(duration_sec * fps):
        # Grey background
        frame = np.ones((height, width, 3), dtype=np.uint8) * 128

        # Moving white square simulates motion
        phase = (frame_idx % (2 * fps)) / (2 * fps)  # 0 → 1 every 2 sec
        square_x = int(phase * (width - 80))
        square_y = height // 2 - 40
        frame[square_y:square_y + 80, square_x:square_x + 80] = 255

        # Add some noise (salt & pepper)
        noise = np.random.randint(0, 255, (height, width, 3), dtype=np.uint8)
        mask = np.random.random((height, width, 1)) < 0.005
        frame = np.where(mask, noise, frame).astype(np.uint8)

        out.write(frame)

    out.release()
    print(f"Test video created: {path} ({duration_sec}s, {fps}fps)")

# ---------------------------------------------------------------------------
# Event sender (async)
# ---------------------------------------------------------------------------


async def send_event(camera_id: str, severity: str, confidence: float) -> None:
    """POST a motion event to the backend API."""
    payload = {
        "camera_id": camera_id,
        "event_type": "motion_detected",
        "severity": severity,
        "confidence": round(confidence, 2),
    }
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            response = await client.post(
                f"{BACKEND_URL}/api/events",
                json=payload,
            )
            print(f"Event sent: {payload} -> {response.status_code}")
        except Exception as exc:
            print(f"Error sending event: {exc}")


# ---------------------------------------------------------------------------
# Main detection loop
# ---------------------------------------------------------------------------


def main() -> None:
    """Open video source and run the motion detection loop."""
    # --- Open capture ---
    if SOURCE == "webcam":
        print(f"Opening webcam (device 0) for camera {CAMERA_ID}")
        cap = cv2.VideoCapture(0)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # low-latency buffer
    else:
        # If the video file doesn't exist, generate a synthetic one
        if not os.path.exists(VIDEO_PATH):
            _generate_test_video(VIDEO_PATH)
        print(f"Opening video file {VIDEO_PATH} for camera {CAMERA_ID}")
        cap = cv2.VideoCapture(VIDEO_PATH)

    if not cap.isOpened():
        print(f"ERROR: Could not open video source ({SOURCE})")
        return

    # --- Video properties ---
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        fps = 20.0
    frame_delay = 1.0 / fps
    print(f"Capture opened: {fps:.1f} FPS, delay={frame_delay:.4f}s")

    # --- Initialise reference frame ---
    ret, first_frame = cap.read()
    if not ret:
        print("ERROR: Could not read first frame")
        cap.release()
        return

    prev_gray = cv2.cvtColor(first_frame, cv2.COLOR_BGR2GRAY)
    prev_gray = cv2.GaussianBlur(prev_gray, (21, 21), 0)

    last_event_time: float = 0.0

    # --- Start MJPEG stream server (background thread) ---
    stream_thread = threading.Thread(target=_start_mjpeg_server, daemon=True)
    stream_thread.start()
    print("Starting motion detection loop (Ctrl+C to stop)...")
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                if SOURCE == "video":
                    print("Video ended, looping from beginning")
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                print("Webcam stream ended")
                break

            # --- Store latest frame as JPEG for MJPEG stream ---
            _, jpeg_buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
            with _jpeg_lock:
                _latest_jpeg = jpeg_buf.tobytes()

            # 1. Convert to grayscale and apply blur
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (21, 21), 0)

            # 2. Frame differencing
            frame_delta = cv2.absdiff(prev_gray, gray)
            thresh = cv2.threshold(frame_delta, 25, 255, cv2.THRESH_BINARY)[1]

            # 3. Dilate to fill gaps
            thresh = cv2.dilate(thresh, None, iterations=2)

            # 4. Count motion pixels
            motion_pixels = cv2.countNonZero(thresh)

            # 5. Store current frame as reference for next iteration
            prev_gray = gray

            # 6. Check threshold and cooldown
            now = time.monotonic()
            if motion_pixels > MOTION_THRESHOLD and (now - last_event_time) >= COOLDOWN_SECONDS:
                # Confidence as normalised ratio (0.0 – 1.0)
                confidence = min(motion_pixels / (MOTION_THRESHOLD * 3), 0.99)

                # Determine severity
                if confidence < 0.3:
                    severity = "low"
                elif confidence < 0.7:
                    severity = "medium"
                else:
                    severity = "high"

                print(
                    f"Motion detected: pixels={motion_pixels}, "
                    f"confidence={confidence:.2f}, severity={severity}"
                )
                asyncio.run(send_event(CAMERA_ID, severity, confidence))
                last_event_time = now

            # Control loop speed to match source FPS
            time.sleep(frame_delay)

    except KeyboardInterrupt:
        print("\nShutdown requested by user")
    finally:
        cap.release()
        print("Capture released. Exiting.")


if __name__ == "__main__":
    main()
