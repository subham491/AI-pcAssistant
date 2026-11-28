"""One photo from a webcam, on demand. Nothing is saved and the camera is released straight after.

The capture runs in a child process (`python -m assistant.desktop.webcam [name]`): QtMultimedia
needs a Qt event loop on its main thread, and the app's own loop belongs to the UI.
"""

from __future__ import annotations

import subprocess
import sys

WARMUP_S = 3.0  # auto-exposure: an OBSBOT Meet 2 in a dim room is still black at 1 s, fine by 3-4 s
NO_FRAME_S = 8.0
MAX_EDGE = 1280


class CameraError(ValueError):
    pass


def grab(name: str = "", timeout: float = 20.0) -> tuple[bytes, str]:
    """(JPEG bytes, camera name). `name` is part of a camera's name; empty = the default camera."""
    try:
        proc = subprocess.run([sys.executable, "-m", __name__, name], capture_output=True, timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise CameraError("the camera did not answer in time") from None
    label, _, jpeg = proc.stdout.partition(b"\n")
    if proc.returncode != 0 or not jpeg:
        lines = proc.stderr.decode(errors="replace").strip().splitlines()
        raise CameraError(lines[-1] if lines else "the camera gave no picture")
    return jpeg, label.decode(errors="replace")


def _child(name: str) -> int:
    import time

    from PySide6.QtCore import QBuffer, QIODevice, Qt, QTimer
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtMultimedia import QCamera, QMediaCaptureSession, QMediaDevices, QVideoSink

    app = QGuiApplication([])
    devices = QMediaDevices.videoInputs()
    wanted = [d for d in devices if name.lower() in d.description().lower()] if name else \
        [d for d in devices if d.isDefault()] or devices
    if not wanted:
        found = ", ".join(d.description() for d in devices) or "none"
        print(f"no camera matches '{name}'; connected: {found}", file=sys.stderr)
        return 1
    device = wanted[0]
    camera, session, sink = QCamera(device), QMediaCaptureSession(), QVideoSink()
    session.setCamera(camera)
    session.setVideoSink(sink)
    started = time.monotonic()
    failed: list[str] = []

    def on_frame(frame) -> None:  # type: ignore[no-untyped-def]
        if time.monotonic() - started < WARMUP_S:
            return
        image = frame.toImage()
        if image.isNull():
            return
        if max(image.width(), image.height()) > MAX_EDGE:
            image = image.scaled(MAX_EDGE, MAX_EDGE, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buf, "JPG", 85)
        sink.videoFrameChanged.disconnect(on_frame)
        sys.stdout.buffer.write(device.description().encode() + b"\n" + bytes(buf.data()))
        sys.stdout.buffer.flush()
        app.quit()

    def on_error(_error, text: str) -> None:  # type: ignore[no-untyped-def]
        failed.append(text or "the camera reported an error (is another app using it?)")
        app.quit()

    sink.videoFrameChanged.connect(on_frame)
    camera.errorOccurred.connect(on_error)
    QTimer.singleShot(int(NO_FRAME_S * 1000), lambda: (failed.append("the camera sent no picture"), app.quit()))
    camera.start()
    app.exec()
    camera.stop()
    if failed:
        print(failed[0], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_child(sys.argv[1] if len(sys.argv) > 1 else ""))
