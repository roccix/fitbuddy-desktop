"""GTK4 / libadwaita front end for FitBuddy Desktop, styled after the FitBuddy Android app."""

from __future__ import annotations

import datetime as dt
import json
import math
import socket
import subprocess
import threading
import urllib.request
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import model  # noqa: E402
from .store import Store  # noqa: E402

APP_ID = "dev.roccix.FitBuddyDesktop"
HERE = Path(__file__).resolve().parent
ICONS = HERE.parent / "data" / "icons"
MEAL_NAMES = ["Breakfast", "Lunch", "Dinner", "Snack"]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def rgb(hex_color: str) -> tuple[float, float, float]:
    return tuple(int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))


ACCENT = rgb("#00E29A")
TRACK = rgb("#3B4A41")
OVER = rgb("#FF8A65")
DIM = rgb("#87988C")
WEIGHT = rgb("#FFCA28")
MACRO_COLORS = {"protein": rgb("#FF7043"), "carbs": rgb("#26A69A"), "fats": rgb("#FFCA28")}


# --- formatting ----------------------------------------------------------------------------------


def today() -> str:
    return dt.date.today().isoformat()


def long_date(date: str) -> str:
    d = dt.date.fromisoformat(date)
    text = f"{WEEKDAYS[d.weekday()]}, {MONTHS[d.month - 1]} {d.day}"
    return text if d.year == dt.date.today().year else f"{text}, {d.year}"


def day_title(date: str) -> str:
    d = dt.date.fromisoformat(date)
    delta = (dt.date.today() - d).days
    return {0: "Today", 1: "Yesterday"}.get(delta, f"{WEEKDAYS[d.weekday()]}, {MONTHS[d.month - 1]} {d.day}")


def hhmm(timestamp_ms: int) -> str:
    return dt.datetime.fromtimestamp(timestamp_ms / 1000).strftime("%H:%M")


def ago(timestamp_ms: int | None) -> str:
    if not timestamp_ms:
        return "never"
    seconds = (dt.datetime.now() - dt.datetime.fromtimestamp(timestamp_ms / 1000)).total_seconds()
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    return dt.datetime.fromtimestamp(timestamp_ms / 1000).strftime("on %b %d at %H:%M")


def parse_time(text: str) -> dt.time | None:
    try:
        hours, minutes = text.strip().replace(".", ":").split(":")
        return dt.time(int(hours), int(minutes))
    except ValueError:
        return None


def parse_number(text: str) -> float | None:
    try:
        return float(text.strip().replace(",", ".")) if text.strip() else None
    except ValueError:
        return None


def default_time(date: str) -> str:
    return dt.datetime.now().strftime("%H:%M") if date == today() else "12:00"


def macros_short(p: int, c: int, f: int) -> str:
    return f"{p}p · {c}c · {f}f"


# --- small widgets -------------------------------------------------------------------------------


def label(text: str, *classes: str, xalign: float = 0, **kw) -> Gtk.Label:
    widget = Gtk.Label(label=text, xalign=xalign, **kw)
    for cls in classes:
        widget.add_css_class(cls)
    return widget


def box(vertical: bool = False, spacing: int = 0, *classes: str, **kw) -> Gtk.Box:
    widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,
                     spacing=spacing, **kw)
    for cls in classes:
        widget.add_css_class(cls)
    return widget


def icon_button(icon: str, tooltip: str, callback, *classes: str) -> Gtk.Button:
    button = Gtk.Button(icon_name=icon, tooltip_text=tooltip, valign=Gtk.Align.CENTER)
    for cls in ("fb-icon-btn", *classes):
        button.add_css_class(cls)
    button.connect("clicked", lambda *_: callback())
    return button


def pill(text: str, primary: bool, callback, icon: str | None = None) -> Gtk.Button:
    content = box(False, 8, halign=Gtk.Align.CENTER)
    if icon:
        content.append(Gtk.Image(icon_name=icon))
    content.append(Gtk.Label(label=text))
    button = Gtk.Button(child=content, hexpand=True)
    button.add_css_class("fb-primary" if primary else "fb-outline")
    button.connect("clicked", lambda *_: callback())
    return button


def badge(icon: str, kind: str) -> Gtk.Widget:
    holder = Gtk.CenterBox(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER, hexpand=False, vexpand=False,
                           center_widget=Gtk.Image(icon_name=icon, pixel_size=22))
    holder.add_css_class("fb-badge")
    holder.add_css_class(f"fb-badge-{kind}")
    return holder


def clickable(widget: Gtk.Widget, callback) -> None:
    click = Gtk.GestureClick()
    click.connect("released", lambda *_: callback())
    widget.add_controller(click)
    widget.set_cursor(Gdk.Cursor.new_from_name("pointer"))


class Field(Gtk.Box):
    """Outlined text field with a caption, like the Material fields in FitBuddy."""

    def __init__(self, caption: str, text: str = "", width_chars: int = 0, numeric: bool = False,
                 placeholder: str = ""):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=width_chars == 0)
        self.append(label(caption, "fb-field-label"))
        self.entry = Gtk.Entry(text=text, placeholder_text=placeholder, hexpand=True)
        if width_chars:
            self.entry.set_width_chars(width_chars)
        if numeric:
            self.entry.set_input_purpose(Gtk.InputPurpose.NUMBER)
            self.entry.set_alignment(1)
        self.entry.add_css_class("fb-field")
        self.append(self.entry)

    @property
    def text(self) -> str:
        return self.entry.get_text()

    @text.setter
    def text(self, value: str):
        self.entry.set_text(value)

    def number(self) -> float:
        return parse_number(self.text) or 0.0


class CalorieRing(Gtk.DrawingArea):
    def __init__(self, fraction: float, over: bool):
        super().__init__(content_width=250, content_height=250, halign=Gtk.Align.CENTER)
        self.fraction, self.over = fraction, over
        self.set_draw_func(self._draw)

    def _draw(self, _area, cr, width, height):
        stroke = 18
        radius = min(width, height) / 2 - stroke / 2 - 2
        cr.set_line_width(stroke)
        cr.set_line_cap(1)
        cr.set_source_rgb(*TRACK)
        cr.arc(width / 2, height / 2, radius, 0, 2 * math.pi)
        cr.stroke()
        if self.fraction > 0:
            cr.set_source_rgb(*(OVER if self.over else ACCENT))
            start = -math.pi / 2
            cr.arc(width / 2, height / 2, radius, start, start + 2 * math.pi * min(self.fraction, 0.9999))
            cr.stroke()


class Bar(Gtk.DrawingArea):
    def __init__(self, color, fraction: float):
        super().__init__(content_height=6, hexpand=True)
        self.color, self.fraction = color, max(0.0, fraction)
        self.set_draw_func(self._draw)

    def _draw(self, _area, cr, width, height):
        r = height / 2

        def pill_path(w):
            cr.new_sub_path()
            cr.arc(r, r, r, math.pi / 2, 3 * math.pi / 2)
            cr.arc(max(w - r, r), r, r, -math.pi / 2, math.pi / 2)
            cr.close_path()

        cr.set_source_rgba(*self.color, 0.28)
        pill_path(width)
        cr.fill()
        if self.fraction > 0:
            cr.set_source_rgb(*(OVER if self.fraction > 1 else self.color))
            pill_path(max(height, width * min(self.fraction, 1)))
            cr.fill()


class BarChart(Gtk.DrawingArea):
    """Daily calories, oldest to newest, with the current target as a dashed line."""

    def __init__(self, days: list[model.Day]):
        super().__init__(content_height=180, hexpand=True)
        self.days = list(reversed(days))
        self.set_draw_func(self._draw)

    def _draw(self, _area, cr, width, height):
        values = [d.eaten for d in self.days]
        targets = [d.targets.kcal for d in self.days if d.targets]
        top = max(values + targets + [1]) * 1.1
        gap = 4
        bar_w = max(2, (width - gap * (len(values) - 1)) / len(values))
        for i, (day, value) in enumerate(zip(self.days, values)):
            if value <= 0:
                continue
            h = (height - 4) * value / top
            x, y = i * (bar_w + gap), height - h
            over = day.targets and value > day.targets.kcal + day.burned
            cr.set_source_rgb(*(OVER if over else ACCENT))
            r = min(bar_w / 2, 5, h)
            cr.new_sub_path()
            cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
            cr.arc(x + bar_w - r, y + r, r, -math.pi / 2, 0)
            cr.line_to(x + bar_w, height)
            cr.line_to(x, height)
            cr.close_path()
            cr.fill()
        cr.set_source_rgba(*DIM, 0.9)
        cr.set_line_width(1.5)
        cr.set_dash([6, 5])
        drawing = False
        for i, day in enumerate(self.days):
            if not day.targets:
                drawing = False
                continue
            y = height - (height - 4) * day.targets.kcal / top
            x = i * (bar_w + gap) - gap / 2
            if drawing:
                cr.line_to(max(x, 0), y)
            else:
                cr.move_to(max(x, 0), y)
                drawing = True
            cr.line_to(min(x + bar_w + gap, width), y)
        cr.stroke()


class LineChart(Gtk.DrawingArea):
    def __init__(self, points: list[tuple[int, float]]):
        super().__init__(content_height=170, hexpand=True)
        self.points = points
        self.set_draw_func(self._draw)

    def _draw(self, _area, cr, width, height):
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        lo, hi = min(ys) - 0.5, max(ys) + 0.5
        x0, x1 = min(xs), max(xs)
        pad = 8

        def pos(p):
            return (pad + (width - 2 * pad) * (p[0] - x0) / max(x1 - x0, 1),
                    pad + (height - 2 * pad) * (1 - (p[1] - lo) / (hi - lo)))

        cr.set_source_rgba(*TRACK, 0.8)
        cr.set_line_width(1)
        for i in range(4):
            y = pad + (height - 2 * pad) * i / 3
            cr.move_to(0, y)
            cr.line_to(width, y)
        cr.stroke()
        cr.set_source_rgb(*WEIGHT)
        cr.set_line_width(2.5)
        cr.set_line_join(1)
        for i, p in enumerate(self.points):
            (cr.move_to if i == 0 else cr.line_to)(*pos(p))
        cr.stroke()
        for p in self.points:
            cr.arc(*pos(p), 3.5, 0, 2 * math.pi)
            cr.fill()


# --- dialogs -------------------------------------------------------------------------------------


class Sheet(Adw.Dialog):
    """FitBuddy-style dialog: heading, scrollable body, optional pill buttons at the bottom."""

    def __init__(self, title: str, width: int = 560, height: int = -1):
        super().__init__(title=title, content_width=width, content_height=height)
        header = Adw.HeaderBar(show_title=False)
        header.pack_start(label(title, "fb-h2", margin_start=12))
        self.body = box(True, 18, margin_start=24, margin_end=24, margin_top=4, margin_bottom=24)
        scroller = Gtk.ScrolledWindow(child=self.body, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      propagate_natural_height=True, vexpand=True)
        self.toolbar = Adw.ToolbarView(content=scroller)
        self.toolbar.add_top_bar(header)
        self.set_child(self.toolbar)

    def actions(self, ok_label: str, on_ok):
        bar = box(False, 12, margin_start=24, margin_end=24, margin_top=8, margin_bottom=20)
        bar.append(pill("Cancel", False, self.close))
        bar.append(pill(ok_label, True, on_ok))
        self.toolbar.add_bottom_bar(bar)


class FoodCard(Gtk.Box):
    def __init__(self, food: model.Food | None, on_remove, on_change):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add_css_class("fb-subcard")
        self.servings = food.servings if food else 1.0
        head = box(False, 8)
        self.name = Field("Food", food.name if food else "", placeholder="e.g. Pasta with tomato sauce")
        head.append(self.name)
        trash = icon_button("fb-trash-symbolic", "Remove food", lambda: on_remove(self), "fb-danger")
        trash.set_valign(Gtk.Align.END)
        head.append(trash)
        self.append(head)
        self.append(label("Totals for this food", "fb-dim", "fb-caption"))
        grid = box(False, 10, homogeneous=True)
        self.kcal = Field("kcal", str(food.calories) if food else "", numeric=True)
        self.protein = Field("Protein", str(food.protein) if food else "", numeric=True)
        self.carbs = Field("Carbs", str(food.carbs) if food else "", numeric=True)
        self.fats = Field("Fats", str(food.fats) if food else "", numeric=True)
        for field in (self.name, self.kcal, self.protein, self.carbs, self.fats):
            field.entry.connect("changed", lambda *_: on_change())
        for field in (self.kcal, self.protein, self.carbs, self.fats):
            grid.append(field)
        self.append(grid)

    def totals(self) -> model.Food:
        return model.Food(self.name.text.strip(), self.servings, round(self.kcal.number()),
                          round(self.protein.number()), round(self.carbs.number()), round(self.fats.number()))


class MealDialog(Sheet):
    def __init__(self, window: "MainWindow", date: str, preset: model.Preset | None = None):
        super().__init__("Build meal", width=600, height=780)
        self.window, self.date = window, date
        self.cards: list[FoodCard] = []

        summary = box(True, 16, "fb-summary")
        self.kcal_label = label("0 kcal", "fb-summary-kcal")
        summary.append(self.kcal_label)
        summary.append(label("Total for this meal", "fb-accent", "fb-caption"))
        macros = box(False, 0, homogeneous=True)
        self.macro_labels = {}
        for key, caption, align in (("protein", "Protein", 0), ("carbs", "Carbs", 0.5),
                                    ("fats", "Fats", 1)):
            col = box(True, 2)
            self.macro_labels[key] = label("0g", "fb-summary-macro", xalign=align)
            col.append(self.macro_labels[key])
            col.append(label(caption, "fb-accent", "fb-caption", xalign=align))
            macros.append(col)
        summary.append(macros)
        self.body.append(summary)

        row = box(False, 12)
        multi = preset is not None and len(preset.foods) > 1
        self.meal_name = Field("Meal name", preset.name if multi else self._guess_meal_name())
        self.time = Field("Time", default_time(date), width_chars=6)
        row.append(self.meal_name)
        row.append(self.time)
        self.body.append(row)
        chips = box(False, 8)
        for name in MEAL_NAMES:
            chip = Gtk.Button(label=name)
            chip.add_css_class("fb-chip")
            chip.connect("clicked", lambda _b, n=name: setattr(self.meal_name, "text", n))
            chips.append(chip)
        self.body.append(chips)

        head = box(False, 8, margin_top=6)
        head.append(label("Foods", "fb-h2", hexpand=True))
        head.append(label(day_title(date), "fb-dim", "fb-caption"))
        self.body.append(head)
        self.food_box = box(True, 14)
        self.body.append(self.food_box)

        more = box(False, 12)
        more.append(pill("Add food", False, lambda: self._add(None), "fb-add-symbolic"))
        presets = window.view.meal_presets()
        if presets:
            content = box(False, 8, halign=Gtk.Align.CENTER)
            content.append(Gtk.Image(icon_name="fb-bookmark-symbolic"))
            content.append(Gtk.Label(label="From saved meal"))
            saved = Gtk.MenuButton(child=content, hexpand=True, popover=self._preset_popover(presets))
            saved.add_css_class("fb-outline")
            more.append(saved)
        self.body.append(more)

        for food in (preset.foods if preset else [None]):
            self._add(food)
        self.actions("Log meal", self._save)

    def _guess_meal_name(self) -> str:
        hour = dt.datetime.now().hour
        return "Breakfast" if hour < 11 else "Lunch" if hour < 16 else "Snack" if hour < 18 else "Dinner"

    def _preset_popover(self, presets: list[model.Preset]) -> Gtk.Popover:
        popover = Gtk.Popover()
        search = Gtk.SearchEntry(placeholder_text="Search")
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        for preset in presets:
            row = Adw.ActionRow(title=GLib.markup_escape_text(preset.name), activatable=True,
                                subtitle=f"{preset.calories} kcal · {len(preset.foods)} foods")
            row.preset = preset
            listbox.append(row)

        def activated(_lb, row):
            if len(self.cards) == 1 and not self.cards[0].totals().name:
                self._remove(self.cards[0])
            for food in row.preset.foods:
                self._add(food)
            popover.popdown()

        listbox.connect("row-activated", activated)
        listbox.set_filter_func(lambda row: search.get_text().lower() in row.preset.name.lower())
        search.connect("search-changed", lambda *_: listbox.invalidate_filter())
        content = box(True, 6, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        content.append(search)
        content.append(Gtk.ScrolledWindow(child=listbox, min_content_height=320, min_content_width=360,
                                          hscrollbar_policy=Gtk.PolicyType.NEVER))
        popover.set_child(content)
        return popover

    def _add(self, food: model.Food | None):
        card = FoodCard(food, self._remove, self._update)
        self.cards.append(card)
        self.food_box.append(card)
        self._update()
        if food is None:
            GLib.idle_add(card.name.entry.grab_focus)

    def _remove(self, card: FoodCard):
        self.cards.remove(card)
        self.food_box.remove(card)
        self._update()

    def _update(self):
        foods = [c.totals() for c in self.cards]
        self.kcal_label.set_label(f"{sum(f.calories for f in foods)} kcal")
        for key in ("protein", "carbs", "fats"):
            self.macro_labels[key].set_label(f"{sum(getattr(f, key) for f in foods)}g")

    def _save(self):
        time = parse_time(self.time.text)
        foods = [f for f in (c.totals() for c in self.cards) if f.name]
        name = self.meal_name.text.strip()
        if time is None:
            self.window.toast("Invalid time: use HH:MM")
            return
        if not name or not foods:
            self.window.toast("Add a meal name and at least one food")
            return
        op = self.window.store.enqueue(
            model.ADD_MEAL, meal=model.meal_op_payload(name, model.timestamp_for(self.date, time), foods))
        self.window.refresh(force=True)
        self.window.toast(f"{name} logged", undo_op=op["id"])
        self.close()


class SavedMealDialog(Sheet):
    """One-tap logging of a saved meal or food, like "Saved meal" on the phone."""

    def __init__(self, window: "MainWindow", date: str):
        super().__init__("Saved meal", width=540, height=660)
        self.window, self.date = window, date
        presets = window.view.meal_presets()
        self.body.append(label(f"One tap to log · {day_title(date)} at {default_time(date)}", "fb-dim"))
        search = Gtk.SearchEntry(placeholder_text="Search")
        self.body.append(search)
        tiles = []
        for preset in presets:
            tile = box(False, 14, "fb-tile", "fb-clickable")
            tile.append(badge("fb-bookmark-symbolic", "meal"))
            text = box(True, 2, hexpand=True, valign=Gtk.Align.CENTER)
            text.append(label(preset.name, "fb-tile-title", ellipsize=3))
            text.append(label(macros_short(sum(f.protein for f in preset.foods), sum(f.carbs for f in preset.foods),
                                           sum(f.fats for f in preset.foods)), "fb-dim", "fb-caption"))
            tile.append(text)
            tile.append(label(f"{preset.calories} kcal", "fb-tile-value", "fb-accent"))
            tile.append(icon_button("fb-edit-symbolic", "Edit before logging",
                                    lambda pr=preset: self._edit(pr)))
            clickable(text, lambda pr=preset: self._log(pr))
            tiles.append((preset, tile))
            self.body.append(tile)
        if not presets:
            self.body.append(label("No saved meals on the phone yet.", "fb-dim"))

        def filter_tiles(*_):
            query = search.get_text().lower()
            for preset, tile in tiles:
                tile.set_visible(query in preset.name.lower())

        search.connect("search-changed", filter_tiles)

    def _log(self, preset: model.Preset):
        timestamp = model.timestamp_for(self.date, parse_time(default_time(self.date)))
        op = self.window.store.enqueue(model.ADD_MEAL, meal=model.meal_op_payload(preset.name, timestamp, preset.foods))
        self.window.refresh(force=True)
        self.window.toast(f"{preset.name} logged", undo_op=op["id"])
        self.close()

    def _edit(self, preset: model.Preset):
        self.close()
        MealDialog(self.window, self.date, preset).present(self.window)


class ExerciseDialog(Sheet):
    def __init__(self, window: "MainWindow", date: str):
        super().__init__("Workout", width=540, height=480)
        self.window, self.date = window, date
        self.name = Field("Activity", placeholder="e.g. Running, Gym")
        self.body.append(self.name)
        recent = window.view.recent_exercises(8)
        if recent:
            flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=8, row_spacing=8,
                               max_children_per_line=4, halign=Gtk.Align.START)
            for name in recent:
                chip = Gtk.Button(label=name)
                chip.add_css_class("fb-chip")
                chip.connect("clicked", lambda _b, n=name: setattr(self.name, "text", n))
                flow.append(chip)
            self.body.append(flow)
        row = box(False, 12, homogeneous=True)
        self.minutes = Field("Duration (min)", "30", numeric=True)
        self.kcal = Field("Calories burned", "", numeric=True)
        self.time = Field("Time", default_time(date))
        for field in (self.minutes, self.kcal, self.time):
            row.append(field)
        self.body.append(row)
        self.body.append(label(day_title(date), "fb-dim", "fb-caption"))
        self.actions("Log", self._save)

    def _save(self):
        name = self.name.text.strip()
        time = parse_time(self.time.text)
        if not name or time is None:
            self.window.toast("Add an activity name and a valid time (HH:MM)")
            return
        op = self.window.store.enqueue(model.ADD_EXERCISE, exercise={
            "activityName": name,
            "timestamp": model.timestamp_for(self.date, time),
            "caloriesBurned": round(self.kcal.number()),
            "durationMinutes": round(self.minutes.number()),
        })
        self.window.refresh(force=True)
        self.window.toast(f"{name} logged", undo_op=op["id"])
        self.close()


class WeightDialog(Sheet):
    def __init__(self, window: "MainWindow", date: str):
        super().__init__("Weight", width=500, height=420)
        self.window, self.date = window, date
        last = window.view.latest_weight()
        row = box(False, 12, homogeneous=True)
        self.weight = Field("Weight (kg)", f"{last.weight:.1f}" if last else "", numeric=True)
        self.fat = Field("Body fat % (optional)",
                         f"{last.body_fat:.1f}" if last and last.body_fat else "", numeric=True)
        row.append(self.weight)
        row.append(self.fat)
        self.body.append(row)
        self.time = Field("Time", default_time(date), width_chars=6)
        self.body.append(self.time)
        self.body.append(label(day_title(date), "fb-dim", "fb-caption"))
        self.actions("Log", self._save)

    def _save(self):
        weight = parse_number(self.weight.text)
        fat = parse_number(self.fat.text)
        time = parse_time(self.time.text)
        if not weight or not 20 <= weight <= 400 or time is None:
            self.window.toast("Enter a valid weight and time (HH:MM)")
            return
        op = self.window.store.enqueue(model.ADD_MEASUREMENT, measurement={
            "timestamp": model.timestamp_for(self.date, time),
            "weightKg": round(weight, 1),
            "bodyFatPct": round(fat, 1) if fat else None,
        })
        self.window.refresh(force=True)
        self.window.toast("Weight logged", undo_op=op["id"])
        self.close()


class LogSheet(Sheet):
    """Entry point like the phone's "Log" bottom sheet."""

    def __init__(self, window: "MainWindow"):
        super().__init__("Log", width=540, height=600)
        self.body.append(label(f"Pick what you're tracking · {day_title(window.date)}", "fb-dim"))
        for title, tiles in (
            ("Food", [("fb-restaurant-symbolic", "Build meal", "Multiple foods", MealDialog),
                      ("fb-bookmark-symbolic", "Saved meal", "One tap", SavedMealDialog)]),
            ("Activity", [("fb-fitness-symbolic", "Workout", "Gym or cardio", ExerciseDialog)]),
            ("Body", [("fb-scale-symbolic", "Weight", "Scale reading", WeightDialog)]),
        ):
            self.body.append(label(title, "fb-section", margin_top=6))
            grid = Gtk.Grid(column_spacing=12, row_spacing=12, column_homogeneous=True)
            for i, (icon, name, sub, dialog) in enumerate(tiles):
                content = box(True, 4)
                content.append(Gtk.Image(icon_name=icon, pixel_size=26, halign=Gtk.Align.START))
                content.append(label(name, "fb-tile-title", margin_top=8))
                content.append(label(sub, "fb-dim", "fb-caption"))
                button = Gtk.Button(child=content)
                button.add_css_class("fb-tilebtn")
                button.connect("clicked", lambda _b, d=dialog: self._open(window, d))
                grid.attach(button, i % 2, i // 2, 1, 1)
            self.body.append(grid)

    def _open(self, window: "MainWindow", dialog):
        self.close()
        dialog(window, window.date).present(window)


def tailscale_names() -> list[str]:
    try:
        out = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=3)
        me = json.loads(out.stdout).get("Self") or {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    dns = (me.get("DNSName") or "").rstrip(".")
    names = [dns.split(".")[0], dns] if dns else []
    return names + [ip for ip in me.get("TailscaleIPs") or [] if ":" not in ip]


def lan_addresses() -> list[str]:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.168.0.1", 9))
            address = sock.getsockname()[0]
        return [] if address.startswith("127.") else [address]
    except OSError:
        return []


class PairDialog(Sheet):
    def __init__(self, window: "MainWindow"):
        super().__init__("Pair phone", width=540, height=660)
        self.window = window
        cfg = window.store.config()
        port = cfg["port"]
        self.body.append(label("On the phone: FitBuddy → Settings → PC sync. Turn sync on, "
                               "enter an address and the pairing code, then Save.", "fb-dim", wrap=True))
        self.body.append(label("Pairing code", "fb-section"))
        code_tile = box(False, 8, "fb-tile")
        self.code = label(cfg["token"], "fb-macro", hexpand=True, selectable=True)
        code_tile.append(self.code)
        code_tile.append(icon_button("edit-copy-symbolic", "Copy", lambda: self._copy(self.code.get_label())))
        code_tile.append(icon_button("view-refresh-symbolic", "Generate a new code", self._regenerate))
        self.body.append(code_tile)

        ts = tailscale_names()
        self.body.append(label("Tailscale address", "fb-section"))
        self.body.append(label("Works from anywhere; traffic is encrypted." if ts
                               else "Tailscale doesn't seem to be running on this PC.", "fb-dim", "fb-caption"))
        for caption, name in zip(("Short name", "Full name", "IP"), ts[:3]):
            self.body.append(self._address(caption, f"{name}:{port}"))
        lan = lan_addresses()
        if lan and cfg.get("allow_lan", False):
            self.body.append(label("Local network (unencrypted)", "fb-section"))
            for address in lan:
                self.body.append(self._address("IP", f"{address}:{port}"))

    def _address(self, caption: str, address: str) -> Gtk.Widget:
        tile = box(False, 8, "fb-tile")
        text = box(True, 2, hexpand=True)
        text.append(label(caption, "fb-dim", "fb-caption"))
        text.append(label(address, "fb-tile-title", selectable=True))
        tile.append(text)
        tile.append(icon_button("edit-copy-symbolic", "Copy", lambda: self._copy(address)))
        return tile

    def _copy(self, text: str):
        Gdk.Display.get_default().get_clipboard().set(text)
        self.window.toast("Copied")

    def _regenerate(self):
        dialog = Adw.AlertDialog(heading="Generate a new code?",
                                 body="The phone stops syncing until you enter the new code.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("regen", "Generate")
        dialog.set_response_appearance("regen", Adw.ResponseAppearance.DESTRUCTIVE)

        def done(_d, response):
            if response == "regen":
                self.code.set_label(self.window.store.regenerate_token())

        dialog.connect("response", done)
        dialog.present(self)


# --- main window ---------------------------------------------------------------------------------


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, store: Store):
        super().__init__(application=app, title="FitBuddy", default_width=820, default_height=940)
        self.store = store
        self.date = today()
        self._today_seen = today()
        self.view = model.View(None, [])
        self.meta: dict = {}
        self._stamp = None
        self._service_ok: bool | None = None
        self._expanded: set[str] = set()

        self.stack = Adw.ViewStack(vexpand=True)
        self.pages: dict[str, Gtk.Box] = {}
        for name, title, icon in (("today", "Today", "fb-today-symbolic"),
                                  ("progress", "Progress", "fb-chart-symbolic"),
                                  ("body", "Body", "fb-scale-symbolic")):
            self.pages[name] = box(True, 18, "fb-page")
            clamp = Adw.Clamp(child=self.pages[name], maximum_size=620, margin_top=8, margin_bottom=110,
                              margin_start=16, margin_end=16)
            self.stack.add_titled_with_icon(Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER),
                                            name, title, icon)
        self.stack.connect("notify::visible-child-name", lambda *_: self._update_header())

        header = Adw.HeaderBar()
        title_box = box(False, 8)
        self.prev_btn = icon_button("go-previous-symbolic", "Previous day (Alt+←)", lambda: self.shift_day(-1))
        self.next_btn = icon_button("go-next-symbolic", "Next day (Alt+→)", lambda: self.shift_day(1))
        titles = box(True, 0, valign=Gtk.Align.CENTER)
        self.title_label = label("Today", "fb-title", xalign=0.5)
        self.subtitle_label = label("", "fb-subtitle", xalign=0.5)
        titles.append(self.title_label)
        titles.append(self.subtitle_label)
        for widget in (self.prev_btn, titles, self.next_btn):
            title_box.append(widget)
        header.set_title_widget(title_box)
        menu = Gio.Menu()
        menu.append("Pair phone", "win.pair")
        menu.append("About", "app.about")
        settings = Gtk.MenuButton(icon_name="fb-settings-symbolic", menu_model=menu, tooltip_text="Settings")
        settings.add_css_class("fb-icon-btn")
        header.pack_end(settings)

        fab_content = box(False, 10)
        fab_content.append(Gtk.Image(icon_name="fb-add-symbolic", pixel_size=22))
        fab_content.append(Gtk.Label(label="Log"))
        fab = Gtk.Button(child=fab_content, halign=Gtk.Align.END, valign=Gtk.Align.END,
                         margin_end=28, margin_bottom=24, tooltip_text="Log (Ctrl+N)", action_name="win.log")
        fab.add_css_class("fb-fab")
        overlay = Gtk.Overlay(child=self.stack)
        overlay.add_overlay(fab)

        toolbar = Adw.ToolbarView(content=overlay)
        toolbar.add_top_bar(header)
        toolbar.add_bottom_bar(Adw.ViewSwitcherBar(stack=self.stack, reveal=True))
        self.toasts = Adw.ToastOverlay(child=toolbar)
        self.set_content(self.toasts)

        for name, callback in (
            ("log", lambda *_: LogSheet(self).present(self)),
            ("pair", lambda *_: PairDialog(self).present(self)),
            ("prev-day", lambda *_: self.shift_day(-1)),
            ("next-day", lambda *_: self.shift_day(1)),
            ("today", lambda *_: self.go_to(today())),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        app.set_accels_for_action("win.log", ["<Control>n"])
        app.set_accels_for_action("win.prev-day", ["<Alt>Left"])
        app.set_accels_for_action("win.next-day", ["<Alt>Right"])
        app.set_accels_for_action("win.today", ["<Alt>Home"])

        self.refresh(force=True)
        GLib.timeout_add_seconds(2, self._poll)
        self._check_service()
        GLib.timeout_add_seconds(15, self._check_service)

    # --- state -----------------------------------------------------------------------------

    def toast(self, text: str, undo_op: str | None = None):
        toast = Adw.Toast(title=text, timeout=4)
        if undo_op:
            toast.set_button_label("Undo")
            toast.connect("button-clicked", lambda *_: (self.store.cancel(undo_op), self.refresh(force=True)))
        self.toasts.add_toast(toast)

    def shift_day(self, days: int):
        new = dt.date.fromisoformat(self.date) + dt.timedelta(days=days)
        if new <= dt.date.today():
            self.go_to(new.isoformat())

    def go_to(self, date: str):
        self.date = date
        self.stack.set_visible_child_name("today")
        self._render()

    def _poll(self):
        if today() != self._today_seen:
            if self.date == self._today_seen:
                self.date = today()
            self._today_seen = today()
            self.refresh(force=True)
        else:
            self.refresh()
        return GLib.SOURCE_CONTINUE

    def _check_service(self):
        port = self.store.config()["port"]

        def probe():
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/health", timeout=1):
                    ok = True
            except OSError:
                ok = False
            GLib.idle_add(self._set_service, ok)

        threading.Thread(target=probe, daemon=True).start()
        return GLib.SOURCE_CONTINUE

    def _set_service(self, ok: bool):
        if ok != self._service_ok:
            self._service_ok = ok
            self._render()

    def refresh(self, force: bool = False):
        stamp = tuple((p.stat().st_mtime_ns if p.exists() else 0)
                      for p in (self.store.dir / n for n in ("snapshot.json", "ops.json", "meta.json")))
        if not force and stamp == self._stamp:
            return
        self._stamp = stamp
        self.view = model.View(self.store.snapshot(), self.store.ops())
        self.meta = self.store.meta()
        self._render()

    def _delete(self, op_type: str, entry, name: str):
        dialog = Adw.AlertDialog(heading=f"Delete “{name}”?",
                                 body="" if entry.pending_op else "It will be deleted on the phone too.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def done(_d, response):
            if response != "delete":
                return
            if entry.pending_op:
                self.store.cancel(entry.pending_op)
            else:
                self.store.enqueue(op_type, targetId=entry.id, targetTimestamp=entry.timestamp)
            self.refresh(force=True)

        dialog.connect("response", done)
        dialog.present(self)

    # --- rendering -------------------------------------------------------------------------

    def _update_header(self):
        page = self.stack.get_visible_child_name()
        on_today = page == "today"
        self.prev_btn.set_visible(on_today)
        self.next_btn.set_visible(on_today)
        self.next_btn.set_sensitive(self.date < today())
        if on_today:
            self.title_label.set_label(day_title(self.date).split(",")[0])
            self.subtitle_label.set_label(long_date(self.date))
        else:
            self.title_label.set_label({"progress": "Progress", "body": "Body"}[page])
            self.subtitle_label.set_label("Last 30 days" if page == "progress" else "Weight and goals")

    def _render(self):
        self._update_header()
        self._render_today(self.pages["today"])
        self._render_progress(self.pages["progress"])
        self._render_body(self.pages["body"])

    def _clear(self, container: Gtk.Box):
        while child := container.get_first_child():
            container.remove(child)

    def _banner(self) -> Gtk.Widget | None:
        if self._service_ok is False:
            text, pair = "The sync service isn't running: systemctl --user start fitbuddy-sync", False
        elif not self.view.has_data:
            text, pair = "No data from the phone yet — pair it to get started.", True
        else:
            return None
        banner = box(False, 12, "fb-banner")
        banner.append(label(text, wrap=True, hexpand=True, valign=Gtk.Align.CENTER))
        if pair:
            button = Gtk.Button(label="Pair", valign=Gtk.Align.CENTER, action_name="win.pair")
            button.add_css_class("fb-outline")
            banner.append(button)
        return banner

    def _sync_footer(self) -> Gtk.Widget:
        parts = [f"Phone synced {ago(self.meta.get('last_contact_at'))}"]
        if pending := self.view.pending_count:
            parts.append(f"{pending} change{'' if pending == 1 else 's'} waiting for the phone")
        return label(" · ".join(parts), "fb-dim", "fb-caption", xalign=0.5, margin_top=8)

    def _render_today(self, page: Gtk.Box):
        self._clear(page)
        if banner := self._banner():
            page.append(banner)
        day = self.view.day(self.date)
        t = day.targets

        ring_card = box(True, 18, "fb-card")
        center = box(True, 0, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        if t:
            budget = t.kcal + day.burned
            left = budget - day.eaten
            ring = CalorieRing(day.eaten / budget if budget else 0, left < 0)
            center.append(label(str(abs(left)), "fb-ring-big", xalign=0.5))
            center.append(label("kcal over" if left < 0 else "kcal left", "fb-dim", xalign=0.5))
        else:
            ring = CalorieRing(0, False)
            center.append(label(str(day.eaten), "fb-ring-big", xalign=0.5))
            center.append(label("kcal", "fb-dim", xalign=0.5))
        overlay = Gtk.Overlay(child=ring)
        overlay.add_overlay(center)
        ring_card.append(overlay)
        stats = box(False, 0, homogeneous=True)
        for value, caption in ((day.eaten, "Eaten"), (day.burned, "Burned"), (t.kcal if t else "—", "Target")):
            col = box(True, 2)
            col.append(label(str(value), "fb-stat", xalign=0.5))
            col.append(label(caption, "fb-dim", xalign=0.5))
            stats.append(col)
        ring_card.append(stats)
        page.append(ring_card)

        macros = box(False, 12, homogeneous=True)
        for key, caption in (("protein", "Protein"), ("carbs", "Carbs"), ("fats", "Fats")):
            eaten = day.macro(key)
            target = getattr(t, key) if t else 0
            card = box(True, 6, "fb-card-small")
            card.append(label(caption, "fb-dim", xalign=0.5))
            if target:
                left = target - eaten
                card.append(label(f"{abs(left)}g", "fb-macro", xalign=0.5))
                card.append(label("over" if left < 0 else "left", "fb-dim", "fb-caption", xalign=0.5))
            else:
                card.append(label(f"{eaten}g", "fb-macro", xalign=0.5))
                card.append(label("eaten", "fb-dim", "fb-caption", xalign=0.5))
            card.append(Bar(MACRO_COLORS[key], eaten / target if target else 0))
            macros.append(card)
        page.append(macros)

        page.append(label("Today's Log" if self.date == today() else "Log", "fb-h2", margin_top=8))
        entries = ([("meal", m) for m in day.meals] + [("exercise", e) for e in day.exercises]
                   + [("weight", w) for w in day.measurements])
        entries.sort(key=lambda item: -item[1].timestamp)
        if not entries:
            page.append(label("Nothing logged yet. Use “Log” to add a meal, "
                              "a workout or your weight.", "fb-dim", wrap=True))
        for kind, entry in entries:
            page.append(self._log_tile(kind, entry))
        if self.view.has_data:
            page.append(self._sync_footer())

    def _log_tile(self, kind: str, entry, title: str | None = None) -> Gtk.Widget:
        outer = box(True, 8, "fb-tile")
        row = box(False, 14)
        if kind == "meal":
            row.append(badge("fb-restaurant-symbolic", "meal"))
            default_title = entry.name if len(entry.foods) <= 1 else f"{entry.name} ({len(entry.foods)})"
            subtitle = f"{hhmm(entry.timestamp)} · {macros_short(entry.protein, entry.carbs, entry.fats)}"
            value, value_cls = f"+{entry.calories}", "fb-accent"
            delete_type, name = model.DELETE_MEAL, entry.name
        elif kind == "exercise":
            row.append(badge("fb-fire-symbolic", "burn"))
            default_title = entry.name
            subtitle = f"{hhmm(entry.timestamp)} · {entry.minutes} min"
            value, value_cls = f"−{entry.calories}", "fb-burn"
            delete_type, name = model.DELETE_EXERCISE, entry.name
        else:
            row.append(badge("fb-scale-symbolic", "weight"))
            default_title = "Weight"
            subtitle = hhmm(entry.timestamp) + (f" · {entry.body_fat:.1f}% body fat" if entry.body_fat else "")
            value, value_cls = f"{entry.weight:.1f} kg", "fb-weight"
            delete_type, name = model.DELETE_MEASUREMENT, f"{entry.weight:.1f} kg"
        text = box(True, 2, hexpand=True, valign=Gtk.Align.CENTER)
        text.append(label(title or default_title, "fb-tile-title", ellipsize=3))
        text.append(label(subtitle, "fb-dim", "fb-caption"))
        row.append(text)
        if entry.pending_op:
            row.append(Gtk.Image(icon_name="fb-cloud-up-symbolic", tooltip_text="Not on the phone yet",
                                 css_classes=["fb-dim"]))
        row.append(label(value, "fb-tile-value", value_cls))
        row.append(icon_button("fb-trash-symbolic", "Delete", lambda: self._delete(delete_type, entry, name)))
        outer.append(row)

        if kind == "meal" and entry.foods:
            key = entry.pending_op or f"meal-{entry.id}"
            foods = box(True, 6, margin_start=60)
            for food in entry.foods:
                line = box(False, 10, "fb-food")
                servings = f"{food.servings:g}× " if food.servings != 1 else ""
                line.append(label(servings + food.name, hexpand=True, ellipsize=3))
                line.append(label(macros_short(food.protein, food.carbs, food.fats), "fb-dim", "fb-caption"))
                line.append(label(f"{food.calories} kcal", "fb-accent"))
                foods.append(line)
            revealer = Gtk.Revealer(child=foods, reveal_child=key in self._expanded,
                                    transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
            outer.append(revealer)
            outer.add_css_class("fb-clickable")

            def toggle():
                show = not revealer.get_reveal_child()
                revealer.set_reveal_child(show)
                (self._expanded.add if show else self._expanded.discard)(key)

            clickable(text, toggle)
        return outer

    def _render_progress(self, page: Gtk.Box):
        self._clear(page)
        if not self.view.has_data:
            page.append(label("History shows up after the first sync with the phone.", "fb-dim", wrap=True))
            return
        days = self.view.history(today(), 30)
        logged = [d for d in days if d.meals]
        card = box(True, 12, "fb-card")
        card.append(label("Calories per day", "fb-h2"))
        card.append(label("The dashed line is your target", "fb-dim", "fb-caption"))
        card.append(BarChart(days))
        page.append(card)

        def avg(values):
            return round(sum(values) / len(values)) if values else 0

        stats = box(False, 12, homogeneous=True)
        for value, caption in ((avg([d.eaten for d in logged]), "avg kcal"),
                               (f"{avg([d.macro('protein') for d in logged])}g", "avg protein"),
                               (len(logged), "days logged"),
                               (sum(d.burned for d in days), "kcal burned")):
            col = box(True, 2, "fb-card-small")
            col.append(label(str(value), "fb-macro", xalign=0.5))
            col.append(label(caption, "fb-dim", "fb-caption", xalign=0.5))
            stats.append(col)
        page.append(stats)

        page.append(label("Days", "fb-h2", margin_top=8))
        weights = {model.date_of(m.timestamp): m.weight for m in self.view.all_measurements()}
        for day in days:
            if not day.meals and not day.exercises and day.date not in weights:
                continue
            tile = box(False, 14, "fb-tile", "fb-clickable")
            text = box(True, 2, hexpand=True)
            text.append(label(day_title(day.date), "fb-tile-title"))
            sub = [macros_short(day.macro("protein"), day.macro("carbs"), day.macro("fats"))]
            if day.burned:
                sub.append(f"−{day.burned} burned")
            if day.date in weights:
                sub.append(f"{weights[day.date]:.1f} kg")
            text.append(label(" · ".join(sub), "fb-dim", "fb-caption"))
            tile.append(text)
            over = day.targets and day.eaten > day.targets.kcal + day.burned
            tile.append(label(f"{day.eaten}" + (f" / {day.targets.kcal}" if day.targets else ""),
                              "fb-tile-value", "fb-error" if over else "fb-accent"))
            tile.append(Gtk.Image(icon_name="go-next-symbolic", css_classes=["fb-dim"]))
            clickable(tile, lambda d=day.date: self.go_to(d))
            page.append(tile)

    def _render_body(self, page: Gtk.Box):
        self._clear(page)
        if not self.view.has_data:
            page.append(label("Body data shows up after the first sync with the phone.", "fb-dim", wrap=True))
            return
        rows = self.view.all_measurements()
        card = box(True, 12, "fb-card")
        head = box(False, 12)
        info = box(True, 2, hexpand=True)
        info.append(label("Current weight", "fb-dim"))
        info.append(label(f"{rows[-1].weight:.1f} kg" if rows else "—", "fb-ring-big"))
        head.append(info)
        if rows:
            older = [m for m in rows if m.timestamp <= rows[-1].timestamp - 30 * 86400_000]
            if older:
                trend = box(True, 2, valign=Gtk.Align.CENTER)
                trend.append(label(f"{rows[-1].weight - older[-1].weight:+.1f} kg", "fb-stat", "fb-weight", xalign=1))
                trend.append(label("in 30 days", "fb-dim", "fb-caption", xalign=1))
                head.append(trend)
        card.append(head)
        recent = [m for m in rows if m.timestamp >= rows[-1].timestamp - 90 * 86400_000] if rows else []
        if len(recent) >= 2:
            card.append(LineChart([(m.timestamp, m.weight) for m in recent]))
            card.append(label("Last 90 days", "fb-dim", "fb-caption"))
        page.append(card)

        t = self.view.targets_for(today())
        if t:
            goal = box(True, 10, "fb-card")
            goal.append(label("Daily targets", "fb-h2"))
            line = box(False, 0, homogeneous=True)
            for value, caption in ((t.kcal, "kcal"), (f"{t.protein}g", "protein"),
                                   (f"{t.carbs}g", "carbs"), (f"{t.fats}g", "fats")):
                col = box(True, 2)
                col.append(label(str(value), "fb-stat", xalign=0.5))
                col.append(label(caption, "fb-dim", "fb-caption", xalign=0.5))
                line.append(col)
            goal.append(line)
            target_weight = (self.view.snapshot.get("profile") or {}).get("targetWeightKg")
            if target_weight:
                goal.append(label(f"Goal weight: {target_weight:.1f} kg", "fb-dim"))
            page.append(goal)

        page.append(label("Measurements", "fb-h2", margin_top=8))
        if not rows:
            page.append(label("No measurements yet.", "fb-dim"))
        for m in reversed(rows[-60:]):
            page.append(self._log_tile("weight", m, title=day_title(model.date_of(m.timestamp))))


class FitBuddyDesktop(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.store = Store()
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", self._about)
        self.add_action(about)

    def do_startup(self):
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        display = Gdk.Display.get_default()
        Gtk.IconTheme.get_for_display(display).add_search_path(str(ICONS))
        css = Gtk.CssProvider()
        css.load_from_path(str(HERE / "style.css"))
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self):
        (self.get_active_window() or MainWindow(self, self.store)).present()

    def _about(self, *_):
        Adw.AboutDialog(
            application_name="FitBuddy Desktop",
            application_icon=APP_ID,
            version="0.2.0",
            comments="Desktop companion for FitBuddy: meals, workouts and weight, "
                     "synced with your phone over Tailscale.",
            license_type=Gtk.License.GPL_3_0,
        ).present(self.get_active_window())


def main() -> int:
    return FitBuddyDesktop().run(None)
