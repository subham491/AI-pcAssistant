"""Speak through a Wi-Fi speaker (a UPnP/DLNA renderer such as a JBL soundbar). Standard library only.

The PC serves the voice as one live WAV stream per reply and tells the speaker to play that URL.
Measured on a JBL Bar 1000 MK2: sound starts about 3 s after the first word is ready, then keeps pace.

A soundbar plays one source at a time, so a reply would cut off the TV. Given the TV's status page, a
reply made while the TV is on plays on the PC instead, with the soundbar turned down to a third meanwhile.
"""

from __future__ import annotations

import http.server
import logging
import queue
import re
import socket
import struct
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urljoin
from xml.sax.saxutils import escape

import numpy as np

log = logging.getLogger(__name__)

LEAD_S = 1.5  # audio handed over ahead of real time: fills the speaker's buffer, then keeps pace
LATENCY_S = 3.0  # how far the speaker's sound trails the stream (measured, see above)
_AVT = "urn:schemas-upnp-org:service:AVTransport:1"
_RC = "urn:schemas-upnp-org:service:RenderingControl:1"
# The volume to put back, kept on disk: if the app is killed mid-reply the speaker would stay turned down.
_DUCKED = Path(tempfile.gettempdir()) / "assistant-ducked-volume.txt"
_SEARCH = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 1\r\n"
           "ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n\r\n").encode()


class NetSpeakerError(RuntimeError):
    pass


def _address_towards(host: str) -> str:
    """This PC's address on the network that reaches `host` (no packet is sent)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((host, 9))
        return str(probe.getsockname()[0])
    finally:
        probe.close()


def find_renderer(name: str, timeout: float = 3.0) -> tuple[str, str, str, str]:
    """(speaker's name, its AVTransport and RenderingControl URLs, this PC's address on its network)."""
    found: list[str] = []
    seen: set[str] = set()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((_address_towards("10.255.255.255"), 0))  # the LAN adapter, not a virtual one
        sock.settimeout(0.3)
        sock.sendto(_SEARCH, ("239.255.255.250", 1900))
        sock.sendto(_SEARCH, ("239.255.255.250", 1900))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, (host, _port) = sock.recvfrom(4096)
            except socket.timeout:
                continue
            location = re.search(r"(?im)^location:\s*(\S+)", data.decode(errors="replace"))
            if not location or location.group(1) in seen:
                continue
            seen.add(location.group(1))
            try:
                xml = urllib.request.urlopen(location.group(1), timeout=2).read().decode(errors="replace")
            except OSError:
                continue
            friendly = re.search(r"<friendlyName>(.*?)</friendlyName>", xml, re.S)
            urls = [re.search(rf"<serviceType>{service}</serviceType>.*?<controlURL>(.*?)</controlURL>", xml, re.S)
                    for service in (_AVT, _RC)]
            if not friendly or not all(urls):
                continue
            found.append(friendly.group(1))
            if name.lower() in friendly.group(1).lower():
                avt, rc = (urljoin(location.group(1), u.group(1)) for u in urls if u)
                return friendly.group(1), avt, rc, _address_towards(host)
    except OSError as exc:
        raise NetSpeakerError(f"could not search the network: {exc}") from exc
    finally:
        sock.close()
    raise NetSpeakerError(f"no Wi-Fi speaker matching '{name}' answered (found: {', '.join(found) or 'none'})")


def _soap(url: str, action: str, args: str = "", service: str = _AVT) -> str:
    body = ('<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="{service}"><InstanceID>0</InstanceID>{args}</u:{action}></s:Body></s:Envelope>')
    request = urllib.request.Request(url, body.encode(), {"Content-Type": 'text/xml; charset="utf-8"',
                                                          "SOAPACTION": f'"{service}#{action}"'})
    try:
        return str(urllib.request.urlopen(request, timeout=4).read().decode(errors="replace"))
    except OSError as exc:  # URLError, HTTPError and timeouts are all OSError
        raise NetSpeakerError(f"the speaker refused {action}: {exc}") from exc


def _tv_is_on(status_url: str) -> bool:
    """A Samsung TV's status page (http://<tv>:8001/api/v2/) answers even in standby and says which it is."""
    try:
        with urllib.request.urlopen(status_url, timeout=1.5) as reply:
            return '"PowerState":"on"' in reply.read().decode(errors="replace").replace(" ", "")
    except OSError:
        # shortcut: a TV whose address moved (DHCP) reads as off, so replies cut its sound again;
        # give the TV a fixed address in the router, or find it by SSDP here.
        return False


def _wav_header(rate: int) -> bytes:
    # Sizes of 0xFFFFFFFF mean "until the connection closes": the stream's length is not known yet.
    return (b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
            + b"data" + struct.pack("<I", 0xFFFFFFFF))


class NetSpeaker:
    """Looks like a sounddevice stream to PocketVoice, but one stream spans a whole reply."""

    def __init__(self, name: str, rate: int, tv_status_url: str = "") -> None:
        self.name, self._control, self._volume_url, me = find_renderer(name)
        self.rate = rate
        self._tv_status_url = tv_status_url
        self._ducked: int | None = None  # the volume to put back after a reply spoken over the TV
        if _DUCKED.exists():  # a reply was cut short by a kill or crash last time
            self._restore(int(_DUCKED.read_text() or 0))
        self._q: queue.Queue[bytes | None] | None = None
        # By URL path, so a reply that ends before the speaker has fetched it is still served in full.
        self._streams: dict[str, queue.Queue[bytes | None]] = {}
        self._sent = self._t0 = 0.0
        self._n = 0
        speaker = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802 - http.server's name
                q = speaker._streams.get(self.path)
                if q is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.end_headers()
                try:
                    self.wfile.write(_wav_header(speaker.rate))
                    while (chunk := q.get()) is not None:
                        self.wfile.write(chunk)
                except OSError:
                    pass  # the speaker hung up: it was told to stop

        self._server = http.server.ThreadingHTTPServer((me, 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, name="netspeaker", daemon=True).start()
        self._base = f"http://{me}:{self._server.server_address[1]}"

    def __enter__(self) -> NetSpeaker:
        return self

    def __exit__(self, *_exc: object) -> None:
        pass  # the stream stays open between sentences; rest() ends it

    def shared(self) -> bool:
        """True while the TV is using this speaker: say the reply on the PC. Decided once per reply,
        and the speaker is turned down to a third until rest()."""
        if self._q is not None:
            return False
        if self._ducked is not None:
            return True
        if not self._tv_status_url or not _tv_is_on(self._tv_status_url):
            return False
        try:
            reply = _soap(self._volume_url, "GetVolume", "<Channel>Master</Channel>", _RC)
            volume = int(re.search(r"<CurrentVolume>(\d+)</CurrentVolume>", reply).group(1))  # type: ignore[union-attr]
            _DUCKED.write_text(str(volume))
            self._set_volume(max(1, volume // 3))
            self._ducked = volume
        except (NetSpeakerError, AttributeError, OSError) as exc:
            log.warning("could not turn the speaker down: %s", exc)
        return True

    def _set_volume(self, volume: int) -> None:
        _soap(self._volume_url, "SetVolume", f"<Channel>Master</Channel><DesiredVolume>{volume}</DesiredVolume>", _RC)

    def _restore(self, volume: int) -> None:
        self._ducked = None
        try:
            if volume:
                self._set_volume(volume)
            _DUCKED.unlink(missing_ok=True)
        except (NetSpeakerError, OSError) as exc:
            log.warning("could not put the speaker's volume back to %s: %s", volume, exc)

    def write(self, frames: np.ndarray) -> None:
        if self._q is None:
            self._start()
        assert self._q is not None
        self._q.put((np.clip(frames, -1.0, 1.0) * 32767).astype("<i2").tobytes())
        self._sent += len(frames) / self.rate
        ahead = self._sent - (time.monotonic() - self._t0)
        if ahead > LEAD_S:  # keep "speaking" in step with what is heard
            time.sleep(ahead - LEAD_S)

    def _start(self) -> None:
        self._n += 1
        url = f"{self._base}/voice{self._n}.wav"
        didl = ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
                '<item id="1" parentID="0" restricted="1"><dc:title>Assistant</dc:title>'
                '<upnp:class>object.item.audioItem.musicTrack</upnp:class>'
                f'<res protocolInfo="http-get:*:audio/wav:*">{url}</res></item></DIDL-Lite>')
        self._q, self._sent, self._t0 = queue.Queue(), 0.0, time.monotonic()
        self._streams = {f"/voice{self._n}.wav": self._q}
        try:
            _soap(self._control, "SetAVTransportURI",
                  f"<CurrentURI>{url}</CurrentURI><CurrentURIMetaData>{escape(didl)}</CurrentURIMetaData>")
            _soap(self._control, "Play", "<Speed>1</Speed>")
        except NetSpeakerError:
            self._q = None
            raise

    def _end(self) -> None:
        q, self._q = self._q, None
        if q is not None:
            q.put(None)

    def abort(self) -> None:
        """Kill switch or barge-in: silence the speaker now."""
        self._end()
        try:
            _soap(self._control, "Stop")
        except NetSpeakerError:
            log.debug("speaker did not take Stop", exc_info=True)

    def rest(self, stop: threading.Event) -> None:
        """The reply is over: end the stream and wait while the speaker plays what it has buffered."""
        if self._ducked is not None:
            self._restore(self._ducked)
        if self._q is None:
            return
        left = self._sent - (time.monotonic() - self._t0) + LATENCY_S
        self._end()
        if stop.wait(max(0.0, left)):
            self.abort()
