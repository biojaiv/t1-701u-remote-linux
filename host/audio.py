"""The laptop's sound to the tablet (MSG_AUDIO).

GStreamer takes what the laptop plays from the default output's monitor
(pulsesrc @DEFAULT_MONITOR@, PipeWire or PulseAudio) as S16LE, 48 kHz stereo
over Wi-Fi and cable, 24 kHz mono over the internet (a quarter of the bytes),
in pieces of 20 ms. The laptop keeps playing the sound itself. Pure silence
(only zero samples) is not sent. Samples arrive in GStreamer's streaming
thread and are handed to the GLib main loop, which owns the connection.
"""
import struct

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

CHUNK_MS = 20
MONITOR = "pulsesrc device=@DEFAULT_MONITOR@ buffer-time=60000 latency-time=20000"


def format_for(route):
    """(rate, channels) for a connection route."""
    return (24000, 1) if route == "internet" else (48000, 2)


def audio_payload(rate, channels, pcm):
    """MSG_AUDIO payload: u32 rate, u8 channels, u8 0, then the samples."""
    return struct.pack("<IBB", rate, channels, 0) + pcm


def chunks(rate, channels, pcm):
    """Splits samples into payloads of at most CHUNK_MS whole frames each,
    without pure silence."""
    frame = 2 * channels
    size = rate * CHUNK_MS // 1000 * frame
    pcm = pcm[:len(pcm) - len(pcm) % frame]
    for start in range(0, len(pcm), size):
        piece = pcm[start:start + size]
        if piece.strip(b"\0"):
            yield audio_payload(rate, channels, piece)


def pipeline_text(rate, channels, source=MONITOR):
    return (f"{source} ! audioconvert ! audioresample ! "
            f"audio/x-raw,format=S16LE,rate={rate},channels={channels},layout=interleaved ! "
            "appsink name=sink emit-signals=true sync=false max-buffers=50 drop=true")


class AudioSender:
    """Captures and hands each piece to send(payload) in the main loop."""

    def __init__(self, send, rate, channels, source=MONITOR, on_error=None):
        Gst.init(None)
        self.send, self.rate, self.channels = send, rate, channels
        self.on_error = on_error
        self.pipeline = Gst.parse_launch(pipeline_text(rate, channels, source))
        self.pipeline.get_by_name("sink").connect("new-sample", self._on_sample)
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_bus_error)
        self.running = False

    def start(self):
        self.running = True
        self.pipeline.set_state(Gst.State.PLAYING)

    def stop(self):
        self.running = False
        self.pipeline.set_state(Gst.State.NULL)
        self.pipeline.get_bus().remove_signal_watch()

    def _on_sample(self, sink):                 # streaming thread
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.EOS
        buffer = sample.get_buffer()
        ok, info = buffer.map(Gst.MapFlags.READ)
        if ok:
            try:
                data = bytes(info.data)
            finally:
                buffer.unmap(info)
            for payload in chunks(self.rate, self.channels, data):
                GLib.idle_add(self._send_in_main_loop, payload)
        return Gst.FlowReturn.OK

    def _send_in_main_loop(self, payload):
        if self.running:
            self.send(payload)
        return False

    def _on_bus_error(self, _bus, message):
        err, _debug = message.parse_error()
        print(f"audio: {err.message}; no sound to the tablet", flush=True)
        self.stop()
        if self.on_error:
            self.on_error(err.message)
