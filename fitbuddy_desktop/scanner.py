"""Webcam barcode scanner: GStreamer camera → zbar, with a live preview in an Adw.Dialog."""

from __future__ import annotations

import gi

gi.require_version("Gst", "1.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gst, Gtk  # noqa: E402

# zbar gets the camera's full resolution: scaled down to the preview size, a barcode held far enough
# for a fixed-focus webcam to see it sharply has bars too thin to decode. Only the preview is scaled.
PIPELINE = ("decodebin3 ! videoconvert ! tee name=t "
            "t. ! queue leaky=downstream max-size-buffers=1 ! zbar cache=true ! fakesink sync=false "
            "t. ! queue leaky=downstream max-size-buffers=1 ! videoscale "
            "! video/x-raw,width=640,height=480,pixel-aspect-ratio=1/1 ! videoconvert ! video/x-raw,format=RGBA "
            "! appsink name=sink emit-signals=true max-buffers=1 drop=true sync=false")


def valid_barcode(code: str) -> bool:
    """EAN-8/UPC-A/EAN-13 check digit, so a blurry misread never reaches the lookup."""
    if not code.isdigit() or len(code) not in (8, 12, 13):
        return False
    digits = [int(c) for c in code.zfill(13)]
    total = sum(d * (3 if i % 2 else 1) for i, d in enumerate(digits[:12]))
    return (10 - total % 10) % 10 == digits[12]


def cameras() -> list[Gst.Device]:
    monitor = Gst.DeviceMonitor()
    monitor.add_filter("Video/Source", None)
    monitor.start()
    devices = monitor.get_devices()
    monitor.stop()
    seen, unique = set(), []
    for device in devices:
        if device.get_display_name() not in seen:
            seen.add(device.get_display_name())
            unique.append(device)
    return unique


class ScannerDialog(Adw.Dialog):
    def __init__(self, on_code, preferred: str | None = None, on_camera=None):
        super().__init__(title="Scan barcode", content_width=560, content_height=560)
        Gst.init(None)
        self.on_code, self.on_camera = on_code, on_camera
        self.pipeline: Gst.Pipeline | None = None
        self._frame_pending = False
        self._done = False

        header = Adw.HeaderBar()
        self.devices = cameras()
        self.picker = Gtk.DropDown.new_from_strings([d.get_display_name() for d in self.devices])
        if len(self.devices) > 1:
            names = [d.get_display_name() for d in self.devices]
            if preferred in names:
                self.picker.set_selected(names.index(preferred))
            self.picker.connect("notify::selected", lambda *_: self._start())
            header.set_title_widget(self.picker)
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, vexpand=True, hexpand=True)
        self.picture.add_css_class("fb-camera")
        self.status = Gtk.Label(label="Starting the camera…", wrap=True,
                                margin_top=12, margin_bottom=16, margin_start=16, margin_end=16)
        self.status.add_css_class("fb-dim")
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.picture)
        content.append(self.status)
        toolbar = Adw.ToolbarView(content=content)
        toolbar.add_top_bar(header)
        self.set_child(toolbar)
        self.connect("closed", lambda *_: self._stop())
        if self.devices:
            GLib.idle_add(self._start)
        else:
            self.status.set_label("No camera found. Type the barcode instead.")

    def _start(self):
        self._stop()
        self.picture.set_paintable(None)
        self.status.set_label("Starting the camera…")
        device = self.devices[self.picker.get_selected()]
        if self.on_camera:
            self.on_camera(device.get_display_name())
        try:
            source = device.create_element(None)
            chain = Gst.parse_bin_from_description(PIPELINE, True)
        except GLib.Error as error:
            self.status.set_label(f"Can't use the camera: {error.message}")
            return GLib.SOURCE_REMOVE
        self.pipeline = Gst.Pipeline()
        self.pipeline.add(source)
        self.pipeline.add(chain)
        source.link(chain)
        chain.get_by_name("sink").connect("new-sample", self._sample)
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._message)
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self.status.set_label("Can't start the camera. Is another app using it?")
        return GLib.SOURCE_REMOVE

    def _stop(self):
        if self.pipeline:
            self.pipeline.get_bus().remove_signal_watch()
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None

    def _sample(self, sink):
        # Streaming thread: copy the frame and hand it to the UI unless one is still waiting.
        sample = sink.emit("pull-sample")
        if sample and not self._frame_pending:
            caps = sample.get_caps().get_structure(0)
            width, height = caps.get_value("width"), caps.get_value("height")
            ok, info = sample.get_buffer().map(Gst.MapFlags.READ)
            if ok:
                data = GLib.Bytes.new(bytes(info.data))
                sample.get_buffer().unmap(info)
                self._frame_pending = True
                GLib.idle_add(self._show, data, width, height)
        return Gst.FlowReturn.OK

    def _show(self, data, width, height):
        self._frame_pending = False
        if self.pipeline:
            if self.picture.get_paintable() is None:
                self.status.set_label("Hold the barcode in front of the camera")
            self.picture.set_paintable(
                Gdk.MemoryTexture.new(width, height, Gdk.MemoryFormat.R8G8B8A8, data, width * 4))
        return GLib.SOURCE_REMOVE

    def _message(self, _bus, message):
        if message.type == Gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            self.status.set_label(f"Camera error: {error.message}")
            self._stop()
        elif message.type == Gst.MessageType.ELEMENT and not self._done:
            structure = message.get_structure()
            if structure and structure.get_name() == "barcode":
                code = structure.get_string("symbol") or ""
                if valid_barcode(code):
                    self._done = True
                    self._stop()
                    self.close()
                    self.on_code(code)
                else:
                    self.status.set_label(f"Read “{code}”, not a product barcode. Try again.")
