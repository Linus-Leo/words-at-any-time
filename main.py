#!/usr/bin/env python3
"""FloatingWordBall: a quiet, desktop-first active-recall widget."""
from __future__ import annotations

import argparse
import copy
import ctypes
import html
import json
import logging
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from ctypes import wintypes
from datetime import date
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

try:
    from PySide6.QtCore import (
        QAbstractNativeEventFilter, QDate, QObject, QPoint, QPointF,
        QRect, QRectF, QSize, Qt, QThread, QTimer, QUrl, Signal, Slot,
    )
    from PySide6.QtGui import (
        QAction, QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QIcon,
        QPainter, QPainterPath, QPalette, QPen, QPixmap, QPolygonF, QTextCharFormat,
        QWheelEvent,
    )
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtWidgets import (
        QApplication, QCalendarWidget, QComboBox, QDialog, QDialogButtonBox, QFrame,
        QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QHBoxLayout, QInputDialog,
        QLabel,
        QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QProgressBar,
        QPushButton, QSizePolicy, QSpinBox, QStyle, QSystemTrayIcon, QTabWidget,
        QToolTip, QVBoxLayout, QWidget,
    )
except ImportError as exc:
    raise SystemExit("缺少 PySide6，请先运行：python -m pip install -r requirements.txt") from exc

from vocab_fetcher import (
    VOCABULARIES, ensure_local_vocab, load_vocab, write_json_atomic,
)

APP_NAME = "FloatingWordBall"
APP_VERSION = "1.0.0"
BASE_DIR = Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
CONFIG_PATH = BASE_DIR / "config.json"
VOCAB_DIR = BASE_DIR / "vocab"
BUNDLED_VOCAB_DIR = RESOURCE_DIR / "vocab"
IMPORTANCE_PATH = RESOURCE_DIR / "importance.json"
MIN_SCALE, MAX_SCALE = 0.6, 2.5
MIN_CAPSULE_WIDTH = 252
SNAP_DISTANCE = 26
EDGE_STRIP = 20
EDGE_TRIGGER = 22
EDGE_POLL_MS = 90
EDGE_HIDE_DELAY_MS = 300
MAX_CAPSULE_WIDTH, MAX_CAPSULE_HEIGHT = 1400, 900
AUTO_WORD_BOOK = "我的单词本"
STAR_THRESHOLDS = (3000, 10000)
THEMES = {
    "green": {
        "label": "青绿", "ball": "#173B38", "accent": "#D6F56D",
        "accent_hover": "#C5E75B", "card": "#FCFDF7", "border": "#DEE5D8",
        "body": "#203D36", "headline": "#173B38", "muted": "#648074",
        "on_ball": "#F5F7ED", "danger": "#F3AAA2",
    },
    "blue": {
        "label": "湖蓝", "ball": "#163B57", "accent": "#7DD3FC",
        "accent_hover": "#5EC4F0", "card": "#F6FBFF", "border": "#D3E5F2",
        "body": "#1B3345", "headline": "#153A56", "muted": "#5C7B91",
        "on_ball": "#F2F9FD", "danger": "#FCA5A5",
    },
    "amber": {
        "label": "琥珀", "ball": "#3D2B1F", "accent": "#FFC46B",
        "accent_hover": "#F2B04D", "card": "#FFFBF3", "border": "#EDE0CB",
        "body": "#3B2F24", "headline": "#3C2A1D", "muted": "#88725C",
        "on_ball": "#FFF8EC", "danger": "#FFB08A",
    },
    "violet": {
        "label": "靛紫", "ball": "#2B2440", "accent": "#C9B6FF",
        "accent_hover": "#B49EF7", "card": "#FBF9FF", "border": "#E2DCF0",
        "body": "#2F2942", "headline": "#2A2340", "muted": "#766E8C",
        "on_ball": "#F8F5FF", "danger": "#F3A6C8",
    },
}
DEFAULTS = {
    "vocabulary": "CET4", "scale": 1.0,
    "opacity": 0.92, "click_through": False, "position": None,
    "theme": "green", "size": None, "dock": None,
    "study": {
        "mode": "sequence", "start_index": 0, "daily_count": 30,
        "custom_words": [], "cursor": 0, "date": "", "book": "",
        "known_today": [], "history": {},
        "counts": {"known": 0, "fuzzy": 0, "unknown": 0}, "ratings": {},
    },
}
LOGGER = logging.getLogger("FloatingWordBall")


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def default_data_dir(portable: bool = False) -> Path:
    """Return a writable directory without ever pointing at Program Files.

    A packaged portable build keeps everything beside the EXE; an installed
    build uses %APPDATA%\\FloatingWordBall. Source runs keep the historical
    repository-local files so existing developer data is untouched.
    """
    override = os.environ.get("FLOATINGWORDBALL_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if _is_frozen():
        executable_dir = Path(sys.executable).resolve().parent
        if portable or (executable_dir / "portable.flag").is_file():
            return executable_dir
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / APP_NAME
    return BASE_DIR


def prepare_data_dir(data_dir: Path, legacy_dir: Path | None = None) -> Path:
    """Create the user-data directory and migrate legacy files once."""
    destination = Path(data_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if legacy_dir is not None:
        source_dir = Path(legacy_dir).expanduser().resolve()
        for name in ("config.json", "wordbooks.json", "details_cache.json"):
            source, target = source_dir / name, destination / name
            if source == target or not source.is_file() or target.exists():
                continue
            try:
                shutil.copy2(source, target)
                LOGGER.info("Migrated legacy %s to %s", name, destination)
            except OSError as exc:
                LOGGER.warning("Could not migrate legacy %s: %s", name, exc)
    return destination


def prepare_vocab_dir(data_dir: Path, bundled_vocab_dir: Path | None = None) -> Path:
    """Use a writable vocabulary copy, seeded from packaged read-only data."""
    destination = Path(data_dir) / "vocab"
    destination.mkdir(parents=True, exist_ok=True)
    source_dir = Path(bundled_vocab_dir or BUNDLED_VOCAB_DIR)
    for name in VOCABULARIES:
        source, target = source_dir / f"{name}.json", destination / f"{name}.json"
        if not target.is_file() and source.is_file():
            try:
                shutil.copy2(source, target)
            except OSError as exc:
                LOGGER.warning("Could not copy bundled vocabulary %s: %s", name, exc)
    ensure_local_vocab(destination)
    return destination


def configure_logging(data_dir: Path) -> Path:
    """Log to the writable data directory so windowed failures are diagnosable."""
    log_path = Path(data_dir) / "FloatingWordBall.log"
    handlers: list[logging.Handler] = []
    try:
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    except OSError as exc:
        log_path = Path(tempfile.gettempdir()) / "FloatingWordBall.log"
        try:
            handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
        except OSError:
            pass
        LOGGER.warning("Could not open the preferred log file: %s", exc)
    if not _is_frozen():
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        handlers=handlers or None,
        force=True,
    )
    return log_path


def theme_palette(name: str) -> dict:
    return dict(THEMES.get(name, THEMES["green"]))

def load_importance(path: Path = IMPORTANCE_PATH) -> dict:
    try:
        with path.open("r", encoding="utf-8-sig") as stream:
            payload = json.load(stream)
        ranks = payload.get("ranks", {}) if isinstance(payload, dict) else {}
        return {str(word).lower(): int(rank) for word, rank in ranks.items()
                if isinstance(word, str) and isinstance(rank, (int, float)) and rank > 0}
    except (OSError, ValueError, TypeError) as exc:
        LOGGER.info("Importance data unavailable: %s", exc)
        return {}


def stars_for(word: str, ranks: dict | None = None) -> int:
    rank = (ranks or {}).get(str(word or "").lower())
    if rank is None:
        return 1
    if rank <= STAR_THRESHOLDS[0]:
        return 3
    if rank <= STAR_THRESHOLDS[1]:
        return 2
    return 1


def star_label(stars: int) -> str:
    return {3: "高频核心词", 2: "常用词", 1: "进阶词"}.get(int(stars or 1), "进阶词")


def _mix_hex(front: str, back: str, amount: float) -> str:
    colour, base = QColor(front), QColor(back)
    ratio = max(0.0, min(1.0, float(amount)))
    return QColor(
        round(colour.red() * ratio + base.red() * (1.0 - ratio)),
        round(colour.green() * ratio + base.green() * (1.0 - ratio)),
        round(colour.blue() * ratio + base.blue() * (1.0 - ratio)),
    ).name()


def _plain_text(value) -> str:
    text = re.sub(r"<[^>]+>", "", str(value or ""))
    return html.unescape(" ".join(text.split()))


def parse_youdao_detail(word: str, payload) -> dict:
    if not isinstance(payload, dict):
        return {}
    ec = payload.get("ec") if isinstance(payload.get("ec"), dict) else {}
    items = ec.get("word") if isinstance(ec.get("word"), list) else []
    base = items[0] if items and isinstance(items[0], dict) else {}
    senses = []
    for group in base.get("trs", []) if isinstance(base.get("trs"), list) else []:
        if not isinstance(group, dict):
            continue
        for item in group.get("tr", []) if isinstance(group.get("tr"), list) else []:
            if not isinstance(item, dict):
                continue
            value = (item.get("l") or {}).get("i") if isinstance(item.get("l"), dict) else ""
            values = value if isinstance(value, list) else [value]
            for line in values:
                line = _plain_text(line)
                if line and line not in senses:
                    senses.append(line)
    forms = []
    for item in base.get("wfs", []) if isinstance(base.get("wfs"), list) else []:
        if not isinstance(item, dict):
            continue
        form = item.get("wf") if isinstance(item.get("wf"), dict) else {}
        name, value = _plain_text(form.get("name")), _plain_text(form.get("value"))
        if name and value:
            forms.append(f"{name} {value}")
    examples = []
    sentences = payload.get("blng_sents_part")
    sentences = sentences if isinstance(sentences, dict) else {}
    for pair in sentences.get("sentence-pair", []) if isinstance(sentences.get("sentence-pair"), list) else []:
        if not isinstance(pair, dict):
            continue
        english = _plain_text(pair.get("sentence") or pair.get("sentence-eng"))
        chinese = _plain_text(pair.get("sentence-translation"))
        if english:
            examples.append({"en": english, "zh": chinese})
        if len(examples) >= 2:
            break
    collins_star = 0
    collins = payload.get("collins")
    if isinstance(collins, dict):
        entries = collins.get("collins_entries")
        if isinstance(entries, list) and entries and isinstance(entries[0], dict):
            try:
                collins_star = max(0, min(5, int(entries[0].get("star", 0))))
            except (TypeError, ValueError):
                collins_star = 0
    if not (senses or forms or examples):
        return {}
    return {"word": str(word or ""), "senses": senses[:4], "forms": forms[:5],
            "examples": examples, "collins_star": collins_star}


def fetch_youdao_detail(word: str, timeout: float = 6.0) -> dict:
    url = "https://dict.youdao.com/jsonapi?q=" + quote(str(word or ""))
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=timeout) as response:
        data = response.read(1024 * 1024 + 1)
    if not data or len(data) > 1024 * 1024:
        return {}
    return parse_youdao_detail(word, json.loads(data.decode("utf-8", "replace")))


def load_detail_cache(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8-sig") as stream:
            payload = json.load(stream)
        entries = payload.get("entries", {}) if isinstance(payload, dict) else {}
        return {str(word).lower(): detail for word, detail in entries.items()
                if isinstance(word, str) and isinstance(detail, dict)}
    except (OSError, ValueError):
        return {}


def save_detail_cache(path: Path, entries: dict) -> None:
    try:
        write_json_atomic(path, {"version": 1, "entries": entries})
    except OSError as exc:
        LOGGER.info("Detail cache could not be saved: %s", exc)



def capsule_min_size(scale: float):
    return (max(MIN_CAPSULE_WIDTH, round(MIN_CAPSULE_WIDTH * scale)),
            max(122, round(122 * scale)))


def default_capsule_size(scale: float):
    minimum_width, minimum_height = capsule_min_size(scale)
    return (max(minimum_width, round(320 * scale)),
            max(minimum_height, round(210 * scale)))


def bounded(value, minimum, maximum, default):
    try:
        result = float(value)
        if not math.isfinite(result):
            raise ValueError
        return max(minimum, min(maximum, result))
    except (TypeError, ValueError):
        return default


def _unique_words(values) -> list:
    words = []
    if isinstance(values, list):
        for word in values:
            if isinstance(word, str) and word and word not in words:
                words.append(word)
    return words


def _counts(value) -> dict:
    value = value if isinstance(value, dict) else {}
    return {key: int(bounded(value.get(key, 0), 0, 10 ** 9, 0))
            for key in ("known", "fuzzy", "unknown")}


def _history(value) -> dict:
    value = value if isinstance(value, dict) else {}
    history = {}
    for day, counts in value.items():
        if not isinstance(day, str) or not isinstance(counts, dict):
            continue
        try:
            date.fromisoformat(day)
        except ValueError:
            continue
        history[day] = _counts(counts)
    return history


def study_config(stored) -> dict:
    study = stored if isinstance(stored, dict) else {}
    mode = study.get("mode") if study.get("mode") in ("sequence", "custom", "book") else "sequence"
    try:
        start_index = max(0, int(study.get("start_index", 0)))
    except (TypeError, ValueError):
        start_index = 0
    try:
        daily_count = max(1, min(1000, int(study.get("daily_count", 30))))
    except (TypeError, ValueError):
        daily_count = 30
    try:
        cursor = max(0, int(study.get("cursor", 0)))
    except (TypeError, ValueError):
        cursor = 0
    ratings = study.get("ratings") if isinstance(study.get("ratings"), dict) else {}
    ratings = {word: rating for word, rating in ratings.items()
               if isinstance(word, str) and rating in ("known", "fuzzy", "unknown")}
    known_today = _unique_words(study.get("known_today"))
    history = _history(study.get("history"))
    date_value = study.get("date") if isinstance(study.get("date"), str) else ""
    counts = _counts(study.get("counts"))
    if "known_today" not in study and "history" not in study:
        known_today = [word for word, rating in ratings.items() if rating == "known"]
        if date_value and any(counts.values()):
            try:
                date.fromisoformat(date_value)
                history.setdefault(date_value, dict(counts))
            except ValueError:
                pass
    return {
        "mode": mode, "start_index": start_index, "daily_count": daily_count,
        "custom_words": _unique_words(study.get("custom_words")), "cursor": cursor,
        "date": date_value, "book": study.get("book") if isinstance(study.get("book"), str) else "",
        "known_today": known_today, "history": history, "counts": counts,
        "ratings": ratings,
    }


def load_config(path: Path = CONFIG_PATH) -> dict:
    config = copy.deepcopy(DEFAULTS)
    try:
        with path.open("r", encoding="utf-8-sig") as stream:
            stored = json.load(stream)
        if isinstance(stored, dict):
            config.update({key: stored[key] for key in DEFAULTS if key in stored})
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        LOGGER.warning("Configuration could not be read: %s", exc)
    if config["vocabulary"] not in VOCABULARIES:
        config["vocabulary"] = "CET4"
    if config["theme"] not in THEMES:
        config["theme"] = "green"
    config["scale"] = bounded(config["scale"], MIN_SCALE, MAX_SCALE, 1.0)
    config["opacity"] = bounded(config["opacity"], 0.25, 1.0, 0.92)
    minimum_width, minimum_height = capsule_min_size(config["scale"])
    size = config["size"]
    if (isinstance(size, list) and len(size) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    and math.isfinite(v) and v > 0 for v in size)):
        config["size"] = [int(max(minimum_width, min(MAX_CAPSULE_WIDTH, size[0]))),
                          int(max(minimum_height, min(MAX_CAPSULE_HEIGHT, size[1])))]
    else:
        config["size"] = None
    if config["dock"] not in ("left", "right"):
        config["dock"] = None
    # Click-through is a momentary aid, never a persisted trap: a restart must
    # always return a capsule the user can click and reach the tray from.
    config["click_through"] = False
    config["study"] = study_config(config.get("study"))
    pos = config["position"]
    if not (isinstance(pos, list) and len(pos) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in pos)):
        config["position"] = None
    return config


def save_config(config: dict, path: Path = CONFIG_PATH) -> None:
    try:
        payload = copy.deepcopy({key: config[key] for key in DEFAULTS})
        payload["click_through"] = False
        write_json_atomic(path, payload)
    except OSError as exc:
        LOGGER.warning("Configuration could not be saved: %s", exc)


def _wordbook_entry(item) -> dict | None:
    if isinstance(item, str):
        item = {"word": item}
    if not isinstance(item, dict):
        return None
    word = item.get("word")
    if not isinstance(word, str) or not word.strip():
        return None
    translation = item.get("translation")
    vocabulary = item.get("vocabulary")
    return {
        "word": word.strip(),
        "translation": translation.strip() if isinstance(translation, str) else "",
        "vocabulary": vocabulary if vocabulary in VOCABULARIES else "",
    }


def load_wordbooks(path: Path) -> dict:
    path = Path(path)
    payload = {}
    try:
        with path.open("r", encoding="utf-8-sig") as stream:
            payload = json.load(stream)
    except (OSError, ValueError):
        payload = {}
    source = payload.get("books") if isinstance(payload, dict) and isinstance(payload.get("books"), dict) else payload
    source = source if isinstance(source, dict) else {}
    result = {}
    if AUTO_WORD_BOOK in source:
        result[AUTO_WORD_BOOK] = []
    for name, items in source.items():
        if not isinstance(name, str) or not name.strip():
            continue
        book = []
        seen = set()
        for item in items if isinstance(items, list) else []:
            entry = _wordbook_entry(item)
            if entry and entry["word"] not in seen:
                seen.add(entry["word"])
                book.append(entry)
        result[name.strip()] = book
    result.setdefault(AUTO_WORD_BOOK, [])
    return result


def save_wordbooks(path: Path, books: dict) -> None:
    payload = {"version": 1, "auto_book": AUTO_WORD_BOOK,
               "books": copy.deepcopy(books)}
    try:
        write_json_atomic(Path(path), payload)
    except OSError as exc:
        LOGGER.warning("Wordbooks could not be saved: %s", exc)


class SpeechWorker(QThread):
    finished_audio = Signal(bytes, str)
    failed = Signal(str)

    def __init__(self, word: str, parent=None):
        super().__init__(parent)
        self.word = word

    def run(self):
        try:
            url = "https://dict.youdao.com/dictvoice?audio=" + quote(self.word) + "&type=2"
            request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(request, timeout=6) as response:
                data = response.read(4 * 1024 * 1024 + 1)
            if not data or len(data) > 4 * 1024 * 1024:
                raise ValueError("empty or oversized audio")
            self.finished_audio.emit(data, self.word)
        except Exception as exc:
            LOGGER.info("Online pronunciation unavailable: %s", exc)
            self.failed.emit(self.word)


class SpeechPlayer(QObject):
    """Youdao audio in a worker; local Windows SAPI fallback never blocks Qt."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.85)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.audio_path = None
        self.worker = None
        self.fallback_worker = None

    def speak(self, word: str):
        if self.worker is not None and self.worker.isRunning():
            return
        self.stop()
        self.worker = SpeechWorker(word, self)
        self.worker.finished_audio.connect(self._play_audio)
        self.worker.failed.connect(self._fallback)
        self.worker.start()

    @Slot(bytes, str)
    def _play_audio(self, data: bytes, word: str):
        try:
            suffix = ".mp3" if data.startswith(b"ID3") or data[:2] == b"\xff\xfb" else ".audio"
            handle, name = tempfile.mkstemp(prefix="floating-word-", suffix=suffix)
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
            self.audio_path = name
            self.player.setSource(QUrl.fromLocalFile(name))
            self.player.play()
        except OSError:
            self._fallback(word)

    @Slot(str)
    def _fallback(self, word: str):
        self.player.stop()
        if sys.platform == "win32":
            self.fallback_worker = LocalSpeechWorker(word, self)
            self.fallback_worker.start()
        else:
            try:
                import pyttsx3
                self.fallback_worker = LocalSpeechWorker(word, self, engine=pyttsx3)
                self.fallback_worker.start()
            except ImportError:
                LOGGER.warning("Install pyttsx3 to enable offline pronunciation on this platform.")

    def stop(self):
        self.player.stop()
        self.player.setSource(QUrl())
        if self.audio_path:
            try:
                os.unlink(self.audio_path)
            except OSError:
                pass
            self.audio_path = None

    def shutdown(self):
        self.stop()
        for worker in (self.worker, self.fallback_worker):
            if worker is not None and worker.isRunning():
                worker.wait(7000)


class LocalSpeechWorker(QThread):
    def __init__(self, word, parent=None, engine=None):
        super().__init__(parent)
        self.word, self.engine = word, engine

    def run(self):
        try:
            if self.engine is not None:
                speaker = self.engine.init()
                speaker.say(self.word)
                speaker.runAndWait()
            else:
                environment = dict(os.environ)
                environment["FLOATING_WORD_BALL_SPEAK"] = self.word
                subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                     "Add-Type -AssemblyName System.Speech; "
                     "$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                     "$voice.Speak($env:FLOATING_WORD_BALL_SPEAK)"],
                    env=environment, timeout=20, check=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
        except Exception as exc:
            LOGGER.warning("Offline pronunciation failed: %s", exc)


class WindowsEventFilter(QAbstractNativeEventFilter):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def nativeEventFilter(self, event_type, message):
        return self.owner.native_event(event_type, message)


class WindowsHotkeys(QObject):
    """RegisterHotKey works in a normal desktop session, without admin rights."""
    triggered = Signal(str)
    MOD_ALT, MOD_NOREPEAT = 0x0001, 0x4000
    WM_HOTKEY = 0x0312

    def __init__(self, widget):
        super().__init__(widget)
        self.widget = widget
        self.registered = {}
        self.event_filter = WindowsEventFilter(self)
        self.available = sys.platform == "win32"
        self.user32 = ctypes.windll.user32 if self.available else None
        if self.available:
            self.user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]

    def start(self):
        if not self.available:
            return
        self.registered = {}
        for name, identifier, key in (("boss", 1, ord("Q")), ("through", 2, ord("W"))):
            self.registered[name] = bool(self.user32.RegisterHotKey(
                int(self.widget.winId()), identifier, self.MOD_ALT | self.MOD_NOREPEAT, key))
        if not all(self.registered.values()):
            LOGGER.warning("A global hotkey is occupied by another application: %s", self.registered)
        QApplication.instance().installNativeEventFilter(self.event_filter)

    def native_event(self, event_type, message):
        if self.available and bytes(event_type) == b"windows_generic_MSG":
            pointer = int(message)
            msg = wintypes.MSG.from_address(pointer)
            if msg.message == self.WM_HOTKEY:
                self.triggered.emit("boss" if msg.wParam == 1 else "through")
                return True, 0
        return False, 0

    def stop(self):
        if self.available:
            QApplication.instance().removeNativeEventFilter(self.event_filter)
            for identifier in (1, 2):
                if self.registered.get("boss" if identifier == 1 else "through"):
                    self.user32.UnregisterHotKey(int(self.widget.winId()), identifier)


class KeyboardHotkeys(QObject):
    triggered = Signal(str)

    def __init__(self):
        super().__init__()
        self.keyboard = None

    def start(self):
        try:
            import keyboard
            self.keyboard = keyboard
            keyboard.add_hotkey("alt+q", lambda: self.triggered.emit("boss"), suppress=False)
            keyboard.add_hotkey("alt+w", lambda: self.triggered.emit("through"), suppress=False)
        except (ImportError, OSError) as exc:
            LOGGER.warning("Global hotkeys unavailable: %s", exc)

    def stop(self):
        if self.keyboard is not None:
            self.keyboard.remove_all_hotkeys()


def create_hotkeys(widget):
    return WindowsHotkeys(widget) if sys.platform == "win32" else KeyboardHotkeys()


class DetailFetcher(QObject):
    ready = Signal(str, dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = set()
        self._lock = threading.Lock()
        self._stopped = False

    def request(self, word: str):
        word = str(word or "").strip()
        if not word or self._stopped:
            return
        with self._lock:
            if word in self._active:
                return
            self._active.add(word)
        threading.Thread(target=self._run, args=(word,), daemon=True).start()

    def _run(self, word: str):
        detail = {}
        try:
            detail = fetch_youdao_detail(word)
        except Exception as exc:
            LOGGER.info("Detailed definition unavailable for %s: %s", word, exc)
        finally:
            with self._lock:
                self._active.discard(word)
                stopped = self._stopped
            if not stopped:
                self.ready.emit(word, detail or {})

    def stop(self):
        with self._lock:
            self._stopped = True


def dialog_side_position(anchor: QRect, size: QSize, area: QRect, gap: int = 20) -> QPoint:
    """Place a dialog beside an anchor, never on top of it when a side fits."""
    margin = 8
    width = max(1, min(size.width(), max(1, area.width() - 2 * margin)))
    height = max(1, min(size.height(), max(1, area.height() - 2 * margin)))
    top = max(area.top() + margin, min(anchor.top(), area.bottom() - margin - height + 1))

    right_x = anchor.right() + 1 + gap
    if right_x + width <= area.right() + 1:
        return QPoint(right_x, top)
    left_x = anchor.left() - gap - width
    if left_x >= area.left() + margin:
        return QPoint(left_x, top)

    centered_x = max(area.left() + margin,
                     min(anchor.center().x() - width // 2,
                         area.right() - margin - width + 1))
    below_y = anchor.bottom() + 1 + gap
    if below_y + height <= area.bottom() - margin + 1:
        return QPoint(centered_x, below_y)
    above_y = anchor.top() - gap - height
    if above_y >= area.top() + margin:
        return QPoint(centered_x, above_y)

    return QPoint(area.center().x() - width // 2, area.center().y() - height // 2)


def present_dialog(dialog: QDialog) -> int:
    """Show a dialog beside the capsule and above its always-on-top window."""
    dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    dialog.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
    dialog.setWindowOpacity(1.0)
    if isinstance(dialog, (QInputDialog, QMessageBox)):
        dialog.adjustSize()
    else:
        hint = dialog.sizeHint()
        dialog.resize(max(dialog.width(), hint.width()),
                      max(dialog.height(), hint.height()))

    parent = dialog.parentWidget()
    anchor = parent.window().frameGeometry() if parent is not None else QRect()
    screen = QGuiApplication.screenAt(anchor.center()) if not anchor.isNull() else None
    screen = screen or QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
    if screen is not None:
        area = screen.availableGeometry()
        if anchor.isNull():
            anchor = QRect(area.center(), QSize(1, 1))
        dialog.move(dialog_side_position(anchor, dialog.size(), area))

    dialog.show()
    QApplication.processEvents()
    dialog.raise_()
    dialog.activateWindow()
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.SetWindowPos(
                int(dialog.winId()), -1, 0, 0, 0, 0,
                0x0001 | 0x0002 | 0x0010)  # NOSIZE | NOMOVE | NOACTIVATE
        except Exception as exc:
            LOGGER.debug("Unable to raise dialog to the topmost band: %s", exc)
    return dialog.exec()


def prompt_text(parent: QWidget, title: str, label: str, text: str = "") -> tuple[str, bool]:
    dialog = QInputDialog(parent)
    dialog.setInputMode(QInputDialog.InputMode.TextInput)
    dialog.setWindowTitle(title)
    dialog.setLabelText(label)
    dialog.setTextValue(text)
    accepted = present_dialog(dialog) == QDialog.DialogCode.Accepted
    return dialog.textValue(), accepted


def show_message(parent: QWidget, icon: QMessageBox.Icon, title: str, text: str):
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    present_dialog(box)


def ask_question(parent: QWidget, title: str, text: str) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    return present_dialog(box) == QMessageBox.StandardButton.Yes


def app_icon(palette: dict | None = None) -> QIcon:
    palette = palette or theme_palette("green")
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(palette["ball"]))
    painter.drawRoundedRect(5, 9, 54, 46, 14, 14)
    painter.setPen(QColor(palette["accent"]))
    font = QFont("Georgia", 25)
    painter.setFont(font)
    painter.drawText(QRect(0, 0, 64, 64), Qt.AlignmentFlag.AlignCenter, "W")
    painter.end()
    return QIcon(pixmap)


class BallWidget(QWidget):
    """The single capsule: word, revealable translation and rating buttons."""

    def __init__(self, scale: float, palette: dict | None = None):
        super().__init__()
        self.scale_factor = float(scale)
        self.palette = palette or theme_palette("green")
        self.word = ""
        self.expanded = False
        self.pinned = False
        self.rating_hover = ""
        self.speaker_hover = False
        self.show_actions = True
        self.detail = {}
        self.stars = 0
        self.current_entry = {}
        self.translation_text = ""
        self.translation = QLabel(self)
        self.translation.setObjectName("translation")
        self.translation.setWordWrap(True)
        self.translation.setTextFormat(Qt.TextFormat.RichText)
        self.translation.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.translation.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.translation.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.translation.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.translation.hide()
        # The window owns every pointer event so the whole capsule can be moved,
        # resized and snapped without child widgets swallowing the drag.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.set_scale(self.scale_factor)
        self.set_palette(self.palette)

    def _padding(self) -> float:
        return max(14.0, 20.0 * self.scale_factor)

    def _top(self) -> float:
        return max(10.0, 13.0 * self.scale_factor)

    def _word_height(self) -> float:
        return max(28.0, 38.0 * self.scale_factor)

    def _gap(self) -> float:
        return max(4.0, 6.0 * self.scale_factor)

    def _bottom(self) -> float:
        return max(10.0, 14.0 * self.scale_factor)

    def _button_height(self) -> float:
        return max(26.0, 30.0 * self.scale_factor)

    def _button_gap(self) -> float:
        return max(5.0, 6.0 * self.scale_factor)

    def speaker_rect(self) -> QRectF:
        s = self.scale_factor
        return QRectF(self.width() - self._padding() - 26.0 * s, self._top(), 26.0 * s, 26.0 * s)

    def stars_rect(self) -> QRectF:
        s = self.scale_factor
        size = max(9.0, 11.0 * s)
        gap = max(2.6, 2.8 * s)
        width = 3 * size + 2 * gap
        speaker = self.speaker_rect()
        return QRectF(speaker.left() - max(7.0, 9.0 * s) - width,
                      self._top() + (self._word_height() - size) / 2.0, width, size)

    def collapsed_height(self) -> int:
        return int(self._top() + self._word_height() + self._bottom() + 2)

    def button_rects(self) -> dict:
        if not self.expanded or not self.show_actions:
            return {}
        width = max(60.0, self.width() - 2 * self._padding())
        gap = self._button_gap()
        add_width = max(24.0, 26.0 * self.scale_factor)
        each = max(1.0, (width - add_width - 3 * gap) / 3.0)
        y = self.height() - self._bottom() - self._button_height()
        x = self._padding()
        rects = {}
        for key in ("known", "fuzzy", "unknown"):
            rects[key] = QRectF(x, y, each, self._button_height())
            x += each + gap
        rects["add"] = QRectF(x, y, add_width, self._button_height())
        return rects

    def content_min_height_for_width(self, width: int | float) -> int:
        text_width = max(30, int(width) - int(2 * self._padding()))
        needed = max(self.translation.heightForWidth(text_width),
                     self.translation.sizeHint().height())
        actions = self._gap() + self._button_height() if self.show_actions else 0.0
        return int(self._top() + self._word_height() + self._gap() + needed
                   + actions + self._bottom() + 2)

    def minimumSizeHint(self):
        width = max(MIN_CAPSULE_WIDTH, round(MIN_CAPSULE_WIDTH * self.scale_factor))
        return QSize(width, self.collapsed_height())

    def _apply_style(self):
        ink = QColor(self.palette["on_ball"])
        self.translation.setStyleSheet(
            f"QLabel {{ background: transparent; border: none;"
            f" color: rgba({ink.red()}, {ink.green()}, {ink.blue()}, 214); }}"
        )

    def set_palette(self, palette: dict):
        self.palette = palette
        self._apply_style()
        self._refresh_translation()
        self.update()

    def set_scale(self, scale: float):
        self.scale_factor = max(0.1, float(scale))
        self.translation.setFont(
            QFont("Microsoft YaHei UI", max(8, round(11.5 * self.scale_factor)))
        )
        self._refresh_translation()
        self.updateGeometry()
        self.update()

    def set_content(self, entry: dict, detail: dict | None = None, stars: int = 0):
        self.current_entry = dict(entry or {})
        self.word = self.current_entry.get("word", "")
        self.translation_text = self.current_entry.get("translation") or "释义暂缺"
        self.detail = detail or {}
        self.stars = max(0, min(3, int(stars or 0)))
        self.show_actions = bool(self.word) and self.word != "今日完成"
        self._refresh_translation()
        self.update()

    def set_detail(self, detail: dict | None):
        self.detail = detail or {}
        self._refresh_translation()
        self.updateGeometry()
        self.update()

    def set_expanded(self, expanded: bool):
        self.expanded = bool(expanded)
        self.translation.setVisible(self.expanded)
        self._layout_translation()
        self.update()

    def set_pinned(self, pinned: bool):
        self.pinned = bool(pinned)
        self.update()

    def set_rating_hover(self, key: str):
        if key != self.rating_hover:
            self.rating_hover = key
            self.update()

    def set_speaker_hover(self, hovered: bool):
        hovered = bool(hovered)
        if hovered != self.speaker_hover:
            self.speaker_hover = hovered
            self.update()

    def _detail_html(self) -> str:
        palette = self.palette
        s = self.scale_factor
        ink = QColor(palette["on_ball"])
        primary = ink.name()
        muted = _mix_hex(palette["on_ball"], palette["ball"], 0.64)
        faint = _mix_hex(palette["on_ball"], palette["ball"], 0.50)
        accent = QColor(palette["accent"]).name()
        senses = [str(line).strip() for line in self.detail.get("senses", [])
                  if str(line).strip()]
        if not senses:
            senses = [line.strip() for line in str(self.translation_text).splitlines() if line.strip()]
        if not senses:
            senses = ["释义暂缺"]
        parts = []
        margin = max(2, round(3 * s))
        for line in senses[:4]:
            parts.append(f'<div style="margin:0 0 {margin}px 0;">{html.escape(line)}</div>')
        for phrase in self.phrases()[:2]:
            parts.append(
                f'<div style="margin-top:{max(3, round(5 * s))}px; color:{muted};">'
                f'<span style="color:{accent};">搭配</span>&nbsp;&nbsp;'
                f'{html.escape(phrase.get("phrase", ""))}'
                f'<span style="color:{faint};"> · {html.escape(phrase.get("translation", ""))}</span></div>')
        forms = self.detail.get("forms") or []
        if forms:
            parts.append(
                f'<div style="margin-top:{max(2, round(4 * s))}px; color:{faint};'
                f' font-size:{max(9, round(10.0 * s))}px;">变形&nbsp;&nbsp;'
                + " · ".join(html.escape(str(form)) for form in forms[:4]) + '</div>')
        return (f'<div style="color:{primary}; line-height:142%;'
                f' font-size:{max(10, round(11.5 * s))}px;">' + "".join(parts) + '</div>')

    def phrases(self) -> list:
        phrases = self.current_entry.get("phrases", []) if isinstance(self.current_entry, dict) else []
        return [item for item in phrases if isinstance(item, dict)]

    def _refresh_translation(self):
        if not hasattr(self, "translation"):
            return
        self.translation.setText(self._detail_html())
        self._layout_translation()

    def _layout_translation(self):
        if not self.expanded:
            self.translation.setGeometry(0, 0, 0, 0)
            return
        y = self._top() + self._word_height() + self._gap()
        width = max(0, self.width() - int(2 * self._padding()))
        actions = self._gap() + self._button_height() if self.show_actions else 0.0
        height = max(0, int(self.height() - y - actions - self._bottom()))
        self.translation.setGeometry(int(self._padding()), int(y), width, height)

    def resizeEvent(self, event):
        self._layout_translation()
        super().resizeEvent(event)

    def _draw_stars(self, painter: QPainter):
        if not self.stars:
            return
        s = self.scale_factor
        rect = self.stars_rect()
        size = rect.height()
        gap = max(2.6, 2.8 * s)
        total = 3.0 * size + 2.0 * gap
        left = rect.center().x() - total / 2.0
        accent = QColor(self.palette["accent"])
        for index in range(3):
            filled = index < self.stars
            centre = QPointF(left + index * (size + gap) + size / 2.0, rect.center().y())
            path = QPainterPath()
            for point in range(10):
                angle = -math.pi / 2.0 + point * math.pi / 5.0
                radius = size / 2.0 if point % 2 == 0 else size / 5.2
                pt = QPointF(centre.x() + math.cos(angle) * radius,
                             centre.y() + math.sin(angle) * radius)
                path.moveTo(pt) if point == 0 else path.lineTo(pt)
            path.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen if filled else QPen(QColor(accent.red(), accent.green(), accent.blue(), 104), max(0.8, 1.0 * s)))
            painter.setBrush(accent if filled else Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

    def _draw_add_icon(self, painter: QPainter, rect: QRectF, hovered: bool):
        s = self.scale_factor
        colour = QColor(self.palette["accent"])
        border = QColor(self.palette["on_ball"])
        border.setAlpha(205 if hovered else 145)
        fill = QColor(colour)
        fill.setAlpha(46 if hovered else 14)
        painter.setPen(QPen(border, max(1.0, 1.15 * s),
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(fill)
        size = min(rect.width() - 5 * s, rect.height() * 0.58)
        size = max(10.0, size)
        x = rect.center().x() - size * 0.46
        y = rect.center().y() - size * 0.40
        depth = size * 0.15
        front = QRectF(x, y, size * 0.86, size * 0.86)
        painter.drawRect(front)
        painter.setBrush(QColor(colour) if hovered else Qt.BrushStyle.NoBrush)
        painter.drawPolygon(QPolygonF([
            QPointF(front.left(), front.top()), QPointF(front.left() + depth, front.top() - depth),
            QPointF(front.right() + depth, front.top() - depth), QPointF(front.right(), front.top()),
        ]))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolygon(QPolygonF([
            QPointF(front.right(), front.top()), QPointF(front.right() + depth, front.top() - depth),
            QPointF(front.right() + depth, front.bottom() - depth), QPointF(front.right(), front.bottom()),
        ]))
        painter.setPen(QPen(QColor(self.palette["on_ball"]), max(1.4, 1.8 * s),
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        center = front.center()
        arm = size * 0.22
        painter.drawLine(QPointF(center.x() - arm, center.y()), QPointF(center.x() + arm, center.y()))
        painter.drawLine(QPointF(center.x(), center.y() - arm), QPointF(center.x(), center.y() + arm))

    def _draw_buttons(self, painter: QPainter):
        rects = self.button_rects()
        s = self.scale_factor
        labels = (("known", "认识"), ("fuzzy", "模糊"), ("unknown", "不认识"))
        for key, label in labels:
            rect = rects.get(key)
            if rect is None:
                continue
            hovered = key == self.rating_hover
            if key == "known":
                colour = QColor(self.palette["accent"])
            elif key == "unknown":
                colour = QColor(self.palette.get("danger", "#F3AAA2"))
            else:
                colour = QColor(self.palette["on_ball"])
            border = QColor(colour)
            border.setAlpha(202 if hovered else 112)
            fill = QColor(colour)
            fill.setAlpha(66 if hovered else 18)
            painter.setPen(QPen(border, max(1.0, 1.2 * s)))
            painter.setBrush(fill)
            painter.drawRoundedRect(rect, 10 * s, 10 * s)
            font_size = max(8, round(10.5 * s))
            font = QFont("Microsoft YaHei UI", font_size)
            font.setBold(key == "known")
            while font_size > 7:
                font.setPointSize(font_size)
                if QFontMetrics(font).horizontalAdvance(label) <= rect.width() - max(8.0, 10.0 * s):
                    break
                font_size -= 1
            painter.setFont(font)
            if key == "known":
                text_colour = QColor(self.palette["accent"])
            elif key == "unknown":
                text_colour = colour
            else:
                text_colour = QColor(self.palette["on_ball"])
            painter.setPen(text_colour)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        add = rects.get("add")
        if add is not None:
            hovered = self.rating_hover == "add"
            fill = QColor(self.palette["on_ball"])
            fill.setAlpha(38 if hovered else 12)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(add, 9 * s, 9 * s)
            self._draw_add_icon(painter, add, hovered)

    def paintEvent(self, event):
        s = self.scale_factor
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette
        margin = max(5.0, 6.0 * s)
        rect = QRectF(margin, margin, max(1.0, self.width() - 2 * margin),
                      max(1.0, self.height() - 2 * margin))
        radius = min(max(1.0, rect.height() / 2.0 - 1.0), max(14.0, 22.0 * s))
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        track = QColor(palette["accent"])
        track.setAlpha(164 if self.pinned else 48)
        painter.setPen(QPen(track, max(1.0, (1.5 if self.pinned else 1.2) * s)))
        painter.setBrush(QColor(palette["ball"]))
        painter.drawPath(path)

        speaker = self.speaker_rect()
        right_edge = self.stars_rect().left() if self.stars else speaker.left()
        word_width = max(30.0, right_edge - self._padding() - 8.0 * s)
        word_size = max(11, round(25 * s))
        font = QFont("Georgia", word_size)
        font.setBold(True)
        metrics = QFontMetrics(font)
        while metrics.horizontalAdvance(self.word) > word_width and word_size > 9:
            word_size -= 1
            font.setPointSize(word_size)
            metrics = QFontMetrics(font)
        shown_word = metrics.elidedText(self.word, Qt.TextElideMode.ElideRight,
                                        max(1, int(word_width)))
        painter.setFont(font)
        painter.setPen(QColor(palette["on_ball"]))
        painter.drawText(QRectF(self._padding(), self._top(), word_width, self._word_height()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, shown_word)

        self._draw_stars(painter)

        if self.speaker_hover:
            hover_disc = QColor(palette["accent"])
            hover_disc.setAlpha(42)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(hover_disc)
            painter.drawEllipse(speaker.adjusted(1.0 * s, 1.0 * s, -1.0 * s, -1.0 * s))

        x, y = speaker.center().x() - 10.0 * s, speaker.center().y()
        painter.setPen(QPen(QColor(palette["accent"]), max(1.2, 1.5 * s),
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(QColor(palette["accent"]))
        painter.drawPolygon(QPolygonF([
            QPointF(x - 8 * s, y - 3 * s), QPointF(x - 4 * s, y - 3 * s),
            QPointF(x + 1 * s, y - 8 * s), QPointF(x + 1 * s, y + 8 * s),
            QPointF(x - 4 * s, y + 3 * s), QPointF(x - 8 * s, y + 3 * s),
        ]))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(QRectF(x - 2 * s, y - 6 * s, 10 * s, 12 * s), -55 * 16, 110 * 16)
        painter.drawArc(QRectF(x - 2 * s, y - 10 * s, 17 * s, 20 * s), -55 * 16, 110 * 16)

        if self.expanded:
            separator = QColor(palette["accent"])
            separator.setAlpha(42)
            line_y = self._top() + self._word_height() + self._gap() / 2.0
            painter.setPen(QPen(separator, max(1.0, 1.0 * s)))
            painter.drawLine(QPointF(self._padding(), line_y),
                             QPointF(self.width() - self._padding(), line_y))
            if self.show_actions:
                self._draw_buttons(painter)

        if self.pinned:
            pin_colour = QColor(palette["accent"])
            pin_colour.setAlpha(210)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(pin_colour)
            painter.drawRoundedRect(QRectF(margin + 3.0 * s, self._top() + 5.0 * s,
                                           max(2.0, 2.4 * s), self._word_height() - 10.0 * s),
                                   1.2 * s, 1.2 * s)

        grip = QColor(palette["accent"])
        grip.setAlpha(105)
        painter.setPen(QPen(grip, max(1.0, 1.4 * s), Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        gx = self.width() - margin - 4.0 * s
        gy = self.height() - margin - 4.0 * s
        for index in range(3):
            offset = 5.0 * s * index
            painter.drawLine(QPointF(gx - offset, gy), QPointF(gx, gy - offset))
        painter.end()


class WordbookDialog(QDialog):
    """Choose an ordered starting point or a custom set of words."""

    def __init__(self, vocab_dir: Path, current_vocab: str, study: dict,
                 palette: dict, parent=None):
        super().__init__(parent)
        self.vocab_dir = Path(vocab_dir)
        self.palette = palette
        self.entries = []
        self.result_mode = "sequence"
        self.result_vocab = current_vocab
        self.result_start = 0
        self.result_count = int(study.get("daily_count", 30))
        self.result_custom = []
        self.setWindowTitle("单词本 · 选择循环")
        self.setModal(True)
        self.resize(660, 640)
        self.setStyleSheet(self._style())
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(12)

        title = QLabel("单词本")
        title.setObjectName("title")
        outer.addWidget(title)
        subtitle = QLabel("按词库顺序选起点，或自选一组词；每天按这里设定的个数循环。")
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        top = QHBoxLayout()
        top.addWidget(QLabel("词库"))
        self.vocab_combo = QComboBox()
        for name in VOCABULARIES:
            self.vocab_combo.addItem(name)
        if current_vocab in VOCABULARIES:
            self.vocab_combo.setCurrentText(current_vocab)
        top.addWidget(self.vocab_combo)
        top.addStretch(1)
        top.addWidget(QLabel("每天循环个数"))
        self.daily_spin = QSpinBox()
        self.daily_spin.setRange(1, 1000)
        self.daily_spin.setValue(max(1, min(1000, self.result_count)))
        self.daily_spin.setToolTip("顺序模式从起点向后取这么多词；自选模式循环勾选的词。")
        top.addWidget(self.daily_spin)
        outer.addLayout(top)

        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        self.tabs.addTab(self._build_ordered_page(study), "按顺序背")
        self.tabs.addTab(self._build_custom_page(study), "自选单词")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("开始循环")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self.vocab_combo.currentTextChanged.connect(self._reload)
        self._reload(self.vocab_combo.currentText())

    def _style(self):
        p = self.palette
        return (
            f"QDialog {{ background: {p['card']}; color: {p['body']}; }}"
            f" QLabel#title {{ color: {p['headline']}; font: 700 22px 'Microsoft YaHei UI'; }}"
            f" QLabel#subtitle {{ color: {p['muted']}; font: 12px 'Microsoft YaHei UI'; }}"
            f" QLabel {{ color: {p['body']}; font: 12px 'Microsoft YaHei UI'; }}"
            f" QComboBox, QSpinBox, QLineEdit {{ background: #FFFFFF; color: {p['body']};"
            f" border: 1px solid {p['border']}; border-radius: 8px; padding: 6px 8px; }}"
            f" QListWidget {{ background: #FFFFFF; color: {p['body']}; border: 1px solid {p['border']};"
            f" border-radius: 10px; padding: 4px; }}"
            f" QListWidget::item {{ padding: 6px 4px; border-radius: 6px; }}"
            f" QListWidget::item:selected {{ background: {p['accent']}; color: {p['ball']}; }}"
            f" QTabWidget::pane {{ border: 1px solid {p['border']}; border-radius: 10px; top: -1px; }}"
            f" QTabBar::tab {{ background: transparent; color: {p['muted']}; padding: 8px 16px; }}"
            f" QTabBar::tab:selected {{ color: {p['headline']}; font-weight: 700;"
            f" border-bottom: 2px solid {p['accent']}; }}"
            f" QPushButton {{ background: {p['ball']}; color: {p['on_ball']}; border: none;"
            f" border-radius: 9px; padding: 8px 18px; font: 600 12px 'Microsoft YaHei UI'; }}"
            f" QPushButton:hover {{ background: {p['accent_hover']}; color: {p['ball']}; }}"
        )

    def _build_ordered_page(self, study: dict):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        self.ordered_search = QLineEdit()
        self.ordered_search.setPlaceholderText("搜索单词或释义")
        self.ordered_search.textChanged.connect(
            lambda text: self._filter(self.ordered_list, text))
        layout.addWidget(self.ordered_search)
        self.ordered_list = QListWidget()
        self.ordered_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.ordered_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        layout.addWidget(self.ordered_list, 1)
        hint = QLabel("选中一个词作为今日起点；之后按词库顺序取设定个数，循环往复。")
        hint.setObjectName("subtitle")
        layout.addWidget(hint)
        return page

    def _build_custom_page(self, study: dict):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        row = QHBoxLayout()
        self.custom_search = QLineEdit()
        self.custom_search.setPlaceholderText("搜索单词或释义")
        self.custom_search.textChanged.connect(
            lambda text: self._filter(self.custom_list, text))
        row.addWidget(self.custom_search, 1)
        select_all = QPushButton("全选可见")
        select_all.clicked.connect(self._check_visible)
        clear = QPushButton("清空")
        clear.clicked.connect(self._clear_checked)
        row.addWidget(select_all)
        row.addWidget(clear)
        layout.addLayout(row)
        self.custom_list = QListWidget()
        self.custom_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.custom_list.itemChanged.connect(self._on_custom_changed)
        layout.addWidget(self.custom_list, 1)
        self.custom_hint = QLabel("勾选要循环的词；每日循环个数从勾选词中按顺序取用。")
        self.custom_hint.setObjectName("subtitle")
        self.custom_hint.setWordWrap(True)
        layout.addWidget(self.custom_hint)
        return page

    def _reload(self, name: str):
        path = self.vocab_dir / f"{name}.json"
        try:
            self.entries = load_vocab(path)
        except (OSError, ValueError) as exc:
            self.entries = []
            self.custom_hint.setText(f"词库读取失败：{exc}")
        study = self.parent().config["study"] if self.parent() is not None else {}
        start = int(study.get("start_index", 0)) if isinstance(study, dict) else 0
        checked = set(study.get("custom_words", [])) if isinstance(study, dict) else set()
        self.ordered_list.blockSignals(True)
        self.custom_list.blockSignals(True)
        self.ordered_list.clear()
        self.custom_list.clear()
        for index, entry in enumerate(self.entries):
            word = entry.get("word", "")
            translation = entry.get("translation", "")
            text = f"{index + 1:04d}   {word}    {translation}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setToolTip(f"{word}\n{translation}")
            self.ordered_list.addItem(item)
            custom = QListWidgetItem(f"{word}    {translation}")
            custom.setData(Qt.ItemDataRole.UserRole, word)
            custom.setFlags(custom.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            custom.setCheckState(Qt.CheckState.Checked if word in checked else Qt.CheckState.Unchecked)
            custom.setToolTip(f"{word}\n{translation}")
            self.custom_list.addItem(custom)
        self.ordered_list.blockSignals(False)
        self.custom_list.blockSignals(False)
        if 0 <= start < self.ordered_list.count():
            self.ordered_list.setCurrentRow(start)
            self.ordered_list.scrollToItem(self.ordered_list.item(start))
        self._update_custom_hint()

    def _filter(self, widget: QListWidget, text: str):
        query = text.strip().lower()
        for row in range(widget.count()):
            item = widget.item(row)
            item.setHidden(bool(query) and query not in item.text().lower())

    def _on_custom_changed(self, item: QListWidgetItem):
        self._update_custom_hint()

    def _update_custom_hint(self):
        count = len(self._checked_words())
        self.custom_hint.setText(
            f"已勾选 {count} 个词；每日循环个数从勾选词中按顺序取用。")

    def _checked_words(self):
        words = []
        for row in range(self.custom_list.count()):
            item = self.custom_list.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                words.append(item.data(Qt.ItemDataRole.UserRole))
        return words

    def _check_visible(self):
        query = self.custom_search.text().strip().lower()
        self.custom_list.blockSignals(True)
        for row in range(self.custom_list.count()):
            item = self.custom_list.item(row)
            if not query or query in item.text().lower():
                item.setCheckState(Qt.CheckState.Checked)
        self.custom_list.blockSignals(False)
        self._update_custom_hint()

    def _clear_checked(self):
        self.custom_list.blockSignals(True)
        for row in range(self.custom_list.count()):
            self.custom_list.item(row).setCheckState(Qt.CheckState.Unchecked)
        self.custom_list.blockSignals(False)
        self._update_custom_hint()

    def _accept(self):
        if not self.entries:
            show_message(self, QMessageBox.Icon.Warning, "单词本", "这个词库还没有可用单词。")
            return
        if self.tabs.currentIndex() == 0:
            item = self.ordered_list.currentItem()
            if item is None:
                show_message(self, QMessageBox.Icon.Warning, "单词本", "请先选中一个起点单词。")
                return
            self.result_mode = "sequence"
            self.result_start = int(item.data(Qt.ItemDataRole.UserRole))
            self.result_custom = []
        else:
            words = self._checked_words()
            if not words:
                show_message(self, QMessageBox.Icon.Warning, "单词本", "请至少勾选一个单词。")
                return
            self.result_mode = "custom"
            self.result_custom = words
            self.result_start = 0
        self.result_count = int(self.daily_spin.value())
        self.result_vocab = self.vocab_combo.currentText()
        self.accept()


class StudyCalendarDialog(QDialog):
    """A month calendar backed by the per-day rating counts."""

    def __init__(self, history: dict, palette: dict, parent=None):
        super().__init__(parent)
        self.palette = palette
        self.history = _history(history)
        self.setWindowTitle("学习日历")
        self.setModal(True)
        self.resize(560, 570)
        self.setStyleSheet(self._style())
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(10)
        title = QLabel("学习日历")
        title.setObjectName("title")
        outer.addWidget(title)
        subtitle = QLabel("每个有记录的日子都会亮起来。点击日期查看当天的认识、模糊和不认识数量。")
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(False)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.calendar.setSelectedDate(QDate.currentDate())
        self.calendar.setMinimumHeight(310)
        outer.addWidget(self.calendar, 1)

        self.month_label = QLabel()
        self.month_label.setObjectName("monthSummary")
        outer.addWidget(self.month_label)
        row = QHBoxLayout()
        self.day_label = QLabel()
        self.day_label.setObjectName("dayTitle")
        row.addWidget(self.day_label)
        row.addStretch(1)
        self.day_total = QLabel()
        self.day_total.setObjectName("dayTotal")
        row.addWidget(self.day_total)
        outer.addLayout(row)
        self.day_detail = QLabel()
        self.day_detail.setObjectName("dayDetail")
        self.day_detail.setWordWrap(True)
        outer.addWidget(self.day_detail)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        close.rejected.connect(self.reject)
        outer.addWidget(close)
        self.calendar.selectionChanged.connect(self._show_day)
        self.calendar.currentPageChanged.connect(self._show_month)
        self._mark_days()
        self._show_month(self.calendar.yearShown(), self.calendar.monthShown())
        self._show_day()

    def _style(self) -> str:
        p = self.palette
        return (
            f"QDialog {{ background: {p['card']}; color: {p['body']}; }}"
            f" QLabel#title {{ color: {p['headline']}; font: 700 22px 'Microsoft YaHei UI'; }}"
            f" QLabel#subtitle {{ color: {p['muted']}; font: 12px 'Microsoft YaHei UI'; }}"
            f" QLabel#monthSummary {{ color: {p['muted']}; font: 12px 'Microsoft YaHei UI'; }}"
            f" QLabel#dayTitle {{ color: {p['headline']}; font: 700 15px 'Microsoft YaHei UI'; }}"
            f" QLabel#dayTotal {{ color: {p['headline']}; font: 700 15px 'Microsoft YaHei UI'; }}"
            f" QLabel#dayDetail {{ color: {p['body']}; font: 13px 'Microsoft YaHei UI'; }}"
            f" QCalendarWidget QWidget {{ background: #FFFFFF; color: {p['body']}; }}"
            f" QCalendarWidget QAbstractItemView:enabled {{ background: #FFFFFF; color: {p['body']};"
            f" selection-background-color: {p['accent']}; selection-color: {p['ball']}; }}"
            f" QCalendarWidget QToolButton {{ color: {p['headline']}; background: transparent;"
            f" border: none; padding: 5px; }}"
            f" QCalendarWidget QMenu {{ background: #FFFFFF; color: {p['body']}; }}"
            f" QPushButton {{ background: {p['ball']}; color: {p['on_ball']}; border: none;"
            f" border-radius: 9px; padding: 8px 18px; font: 600 12px 'Microsoft YaHei UI'; }}"
            f" QPushButton:hover {{ background: {p['accent_hover']}; color: {p['ball']}; }}"
        )

    def _mark_days(self):
        for iso, counts in self.history.items():
            total = sum(counts.values())
            if not total:
                continue
            day = QDate.fromString(iso, "yyyy-MM-dd")
            if not day.isValid():
                continue
            fmt = QTextCharFormat()
            colour = QColor(self.palette["accent"])
            colour.setAlpha(105)
            fmt.setBackground(colour)
            fmt.setFontWeight(QFont.Weight.Bold)
            self.calendar.setDateTextFormat(day, fmt)

    def _show_month(self, year: int, month: int):
        prefix = f"{year:04d}-{month:02d}-"
        days = 0
        total = 0
        for iso, counts in self.history.items():
            if iso.startswith(prefix):
                value = sum(counts.values())
                if value:
                    days += 1
                    total += value
        self.month_label.setText(f"本月有 {days} 天留下记录，共点击 {total} 次。")

    def _show_day(self):
        chosen = self.calendar.selectedDate()
        iso = chosen.toString("yyyy-MM-dd")
        counts = self.history.get(iso, {"known": 0, "fuzzy": 0, "unknown": 0})
        weekdays = ("一", "二", "三", "四", "五", "六", "日")
        weekday = weekdays[chosen.dayOfWeek() - 1]
        self.day_label.setText(f"{chosen.toString('yyyy年M月d日')} · 周{weekday}")
        total = sum(counts.values())
        self.day_total.setText(f"{total} 次点击")
        if total:
            self.day_detail.setText(f"认识 {counts['known']}    模糊 {counts['fuzzy']}    不认识 {counts['unknown']}")
        else:
            self.day_detail.setText("这天还没有背诵记录。")


class MyWordbooksDialog(QDialog):
    """Create, rename and inspect the user's wordbooks."""

    def __init__(self, books: dict, palette: dict, parent=None):
        super().__init__(parent)
        self.palette = palette
        self.books = books if isinstance(books, dict) else {}
        self.books.setdefault(AUTO_WORD_BOOK, [])
        self.changed = False
        self.result_book = None
        self.setWindowTitle("我的单词本")
        self.setModal(True)
        self.resize(720, 560)
        self.setStyleSheet(self._style())
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(10)
        title = QLabel("我的单词本")
        title.setObjectName("title")
        outer.addWidget(title)
        subtitle = QLabel("不认识词会自动收进「我的单词本」。也可以新建自己的本子，再把词加进去。")
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        body = QHBoxLayout()
        body.setSpacing(14)
        left = QVBoxLayout()
        left.addWidget(QLabel("单词本"))
        self.books_list = QListWidget()
        self.books_list.setMinimumWidth(210)
        self.books_list.currentItemChanged.connect(self._reload_words)
        left.addWidget(self.books_list, 1)
        book_buttons = QHBoxLayout()
        self.new_button = QPushButton("新建")
        self.rename_button = QPushButton("重命名")
        self.delete_button = QPushButton("删除")
        for button in (self.new_button, self.rename_button, self.delete_button):
            book_buttons.addWidget(button)
            button.setProperty("secondary", True)
        left.addLayout(book_buttons)
        body.addLayout(left)

        right = QVBoxLayout()
        self.words_title = QLabel("单词")
        right.addWidget(self.words_title)
        self.words_list = QListWidget()
        self.words_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        right.addWidget(self.words_list, 1)
        self.empty_label = QLabel("还没有单词。背单词时点「不认识」右侧的立方体加号，就能收进来。")
        self.empty_label.setObjectName("subtitle")
        self.empty_label.setWordWrap(True)
        right.addWidget(self.empty_label)
        word_row = QHBoxLayout()
        self.remove_button = QPushButton("移出单词本")
        self.remove_button.setProperty("secondary", True)
        word_row.addWidget(self.remove_button)
        word_row.addStretch(1)
        right.addLayout(word_row)
        body.addLayout(right, 1)
        outer.addLayout(body, 1)

        footer = QHBoxLayout()
        self.start_button = QPushButton("开始背这个单词本")
        footer.addWidget(self.start_button)
        footer.addStretch(1)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        close.rejected.connect(self.reject)
        footer.addWidget(close)
        outer.addLayout(footer)

        self.new_button.clicked.connect(self._new_book)
        self.rename_button.clicked.connect(self._rename_book)
        self.delete_button.clicked.connect(self._delete_book)
        self.remove_button.clicked.connect(self._remove_word)
        self.start_button.clicked.connect(self._start)
        self._reload_books()

    def _style(self) -> str:
        p = self.palette
        return (
            f"QDialog {{ background: {p['card']}; color: {p['body']}; }}"
            f" QLabel#title {{ color: {p['headline']}; font: 700 22px 'Microsoft YaHei UI'; }}"
            f" QLabel#subtitle {{ color: {p['muted']}; font: 12px 'Microsoft YaHei UI'; }}"
            f" QLabel {{ color: {p['body']}; font: 12px 'Microsoft YaHei UI'; }}"
            f" QListWidget {{ background: #FFFFFF; color: {p['body']}; border: 1px solid {p['border']};"
            f" border-radius: 10px; padding: 4px; }}"
            f" QListWidget::item {{ padding: 7px 5px; border-radius: 6px; }}"
            f" QListWidget::item:selected {{ background: {p['accent']}; color: {p['ball']}; }}"
            f" QPushButton {{ background: {p['ball']}; color: {p['on_ball']}; border: none;"
            f" border-radius: 9px; padding: 8px 18px; font: 600 12px 'Microsoft YaHei UI'; }}"
            f" QPushButton:hover {{ background: {p['accent_hover']}; color: {p['ball']}; }}"
            f" QPushButton[secondary='true'] {{ background: transparent; color: {p['headline']};"
            f" border: 1px solid {p['border']}; }}"
        )

    def _book_names(self) -> list:
        return [AUTO_WORD_BOOK] + [name for name in self.books if name != AUTO_WORD_BOOK]

    def _selected_book(self) -> str:
        item = self.books_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else ""

    def _reload_books(self, select: str | None = None):
        wanted = select or self._selected_book() or AUTO_WORD_BOOK
        self.books_list.blockSignals(True)
        self.books_list.clear()
        for name in self._book_names():
            item = QListWidgetItem(f"{name}    · {len(self.books.get(name, []))} 词")
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.books_list.addItem(item)
            if name == wanted:
                self.books_list.setCurrentItem(item)
        self.books_list.blockSignals(False)
        if self.books_list.currentRow() < 0:
            self.books_list.setCurrentRow(0)
        self._reload_words()

    def _reload_words(self, *_args):
        name = self._selected_book()
        words = self.books.get(name, []) if name else []
        self.books_list.setEnabled(True)
        self.words_title.setText(f"{name} · {len(words)} 词" if name else "单词")
        self.words_list.clear()
        for entry in words:
            text = f"{entry['word']}    {entry['translation']}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, entry["word"])
            item.setToolTip(f"{entry['word']}\n{entry['translation']}")
            self.words_list.addItem(item)
        self.empty_label.setVisible(not words)
        self.words_list.setVisible(bool(words))
        self.remove_button.setEnabled(bool(words))
        self.start_button.setEnabled(bool(words))
        is_auto = name == AUTO_WORD_BOOK
        self.rename_button.setEnabled(bool(name) and not is_auto)
        self.delete_button.setEnabled(bool(name) and not is_auto)

    def _new_book(self):
        name, ok = prompt_text(self, "新建单词本", "单词本名称：")
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        if name in self.books:
            show_message(self, QMessageBox.Icon.Information, "单词本已存在", f"已经有一个叫「{name}」的单词本。")
            return
        self.books[name] = []
        self.changed = True
        self._reload_books(name)

    def _rename_book(self):
        name = self._selected_book()
        if not name or name == AUTO_WORD_BOOK:
            return
        new_name, ok = prompt_text(self, "重命名单词本", "新名称：", text=name)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == name:
            return
        if new_name in self.books:
            show_message(self, QMessageBox.Icon.Information, "单词本已存在", f"已经有一个叫「{new_name}」的单词本。")
            return
        items = [(new_name if key == name else key, value)
                 for key, value in self.books.items()]
        self.books.clear()
        self.books.update(items)
        self.changed = True
        self._reload_books(new_name)

    def _delete_book(self):
        name = self._selected_book()
        if not name or name == AUTO_WORD_BOOK:
            return
        if not ask_question(self, "删除单词本", f"删除「{name}」及其中的单词？"):
            return
        self.books.pop(name, None)
        self.changed = True
        self._reload_books()

    def _remove_word(self):
        name = self._selected_book()
        item = self.words_list.currentItem()
        if not name or item is None:
            return
        word = item.data(Qt.ItemDataRole.UserRole)
        self.books[name] = [entry for entry in self.books.get(name, []) if entry["word"] != word]
        self.changed = True
        self._reload_books(name)

    def _start(self):
        name = self._selected_book()
        if not name or not self.books.get(name):
            return
        self.result_book = name
        self.accept()


class FloatingWindow(QWidget):
    def __init__(self, config: dict, vocab_dir: Path = VOCAB_DIR, config_path: Path = CONFIG_PATH,
                 fetch_details: bool = True):
        super().__init__()
        self.config = config
        self.config_path = Path(config_path)
        self.wordbooks_path = self.config_path.with_name("wordbooks.json")
        self.vocab_dir = vocab_dir
        self.wordbooks = load_wordbooks(self.wordbooks_path)
        self.detail_cache_path = self.config_path.with_name("details_cache.json")
        self.detail_cache = load_detail_cache(self.detail_cache_path)
        self.detail_misses = set()
        self.importance = load_importance()
        self.fetch_details = bool(fetch_details)
        self.detail_fetcher = DetailFetcher(self) if self.fetch_details else None
        if self.detail_fetcher is not None:
            self.detail_fetcher.ready.connect(self._apply_detail)
        self.book_menu_open = False
        self.entries = []
        self.deck = []
        self.deck_index = 0
        self.current_index = 0
        self.current_entry = None
        self.boss_hidden = False
        self.quitting = False
        self.hovered = False
        self.expanded = False
        self.pinned = False
        self.dragging = False
        self.drag_origin = None
        self.drag_moved = False
        self.press_global = None
        self.resize_edge = None
        self.resize_origin_global = None
        self.resize_origin_rect = None
        self.resize_moved = False
        self.rating_pressed = None
        self.pin_before_press = None
        self.dock_side = config.get("dock")
        self.dock_hidden = False
        self.dock_area = QRect()
        self.palette = theme_palette(config.get("theme", "green"))
        self.expanded_size = self._target_size()
        self.setWindowTitle("FloatingWordBall")
        self.setWindowIcon(app_icon(self.palette))
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        if sys.platform == "win32":
            style = ctypes.windll.user32.GetWindowLongW(int(self.winId()), -20)
            ctypes.windll.user32.SetWindowLongW(int(self.winId()), -20, style | 0x00000020 | 0x00080000)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.base_tooltip = "悬停看释义 · 单击固定/取消 · 拖动移动 · 拖边缘调整大小 · 滚轮换词 · 双击换词 · 右键设置 · Esc 逐级退出"
        self.setToolTip(self.base_tooltip)

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        self.ball = BallWidget(config["scale"], self.palette)
        self.layout.addWidget(self.ball)

        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.opacity_effect.setOpacity(config["opacity"])
        self.setGraphicsEffect(self.opacity_effect)
        self.speech = SpeechPlayer(self)
        self._build_tray()
        self._load_vocab(config["vocabulary"])
        self._init_study()
        self._apply_geometry(config.get("position"))
        self.show_current()
        self.set_click_through(config["click_through"], persist=False)
        self.hotkeys = create_hotkeys(self)
        self.hotkeys.triggered.connect(self._hotkey)
        self.hotkeys.start()
        registered = getattr(self.hotkeys, "registered", {})
        if registered and not all(registered.values()):
            self.tray.showMessage(
                "全局快捷键被占用",
                "Alt+Q 或 Alt+W 未能注册。仍可用托盘图标右键菜单关闭穿透或恢复显示。",
                QSystemTrayIcon.MessageIcon.Warning, 6000)
        self.day_timer = QTimer(self)
        self.day_timer.setInterval(60_000)
        self.day_timer.timeout.connect(self._day_tick)
        self.day_timer.start()
        self.edge_hide_timer = QTimer(self)
        self.edge_hide_timer.setSingleShot(True)
        self.edge_hide_timer.setInterval(EDGE_HIDE_DELAY_MS)
        self.edge_hide_timer.timeout.connect(self._edge_hide_now)
        self.edge_timer = QTimer(self)
        self.edge_timer.setInterval(EDGE_POLL_MS)
        self.edge_timer.timeout.connect(self._edge_tick)
        self.edge_timer.start()

    def _save(self):
        self.config["study"]["cursor"] = self.deck_index
        save_config(self.config, self.config_path)

    def _day_tick(self):
        if self._ensure_today():
            self.show_current()
            self._save()

    def _load_vocab(self, name: str):
        path = self.vocab_dir / f"{name}.json"
        try:
            ensure_local_vocab(self.vocab_dir)
            self.entries = load_vocab(path)
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"词库 {name} 无法读取：{exc}\n词库目录：{self.vocab_dir}") from exc
        self.config["vocabulary"] = name
        self.vocab_action.setText(f"{name} · {len(self.entries)} 词")
        for key, action in self.vocab_actions.items():
            action.setChecked(key == name)

    def _ensure_today(self) -> bool:
        study = self.config["study"]
        today = date.today().isoformat()
        if study.get("date") == today:
            return False
        study["date"] = today
        study["cursor"] = 0
        study["known_today"] = []
        study["counts"] = {"known": 0, "fuzzy": 0, "unknown": 0}
        self._rebuild_deck()
        self._save()
        return True

    def _history_counts(self, day: str | None = None) -> dict:
        study = self.config["study"]
        day = day or date.today().isoformat()
        history = study.setdefault("history", {})
        return history.setdefault(day, {"known": 0, "fuzzy": 0, "unknown": 0})

    def _init_study(self):
        self._ensure_today()
        self._rebuild_deck()

    def _rebuild_deck(self):
        self.deck = []
        self.deck_index = 0
        study = self.config["study"]
        known = set(study.get("known_today", []))
        count = max(1, min(int(study.get("daily_count", 30)), 1000))
        if study.get("mode") == "book":
            chosen = [dict(entry) for entry in self.wordbooks.get(study.get("book", ""), [])
                      if entry.get("word") and entry["word"] not in known]
            self.deck = chosen[:count]
        elif study.get("mode") == "custom":
            by_word = {entry["word"]: entry for entry in self.entries}
            requested = [str(word) for word in study.get("custom_words", []) if str(word)]
            chosen = [by_word[word] for word in requested
                      if word in by_word and word not in known]
            if requested:
                self.deck = chosen[:count]
            else:
                self.deck = [entry for entry in self.entries if entry["word"] not in known][:count]
        else:
            if not self.entries:
                return
            count = min(count, len(self.entries))
            start = int(study.get("start_index", 0)) % len(self.entries)
            chosen = [self.entries[(start + offset) % len(self.entries)]
                      for offset in range(count)]
            self.deck = [entry for entry in chosen if entry["word"] not in known]
        self.deck_index = int(study.get("cursor", 0)) % max(1, len(self.deck))

    def _build_tray(self):
        self.tray = QSystemTrayIcon(app_icon(self.palette), self)
        self.tray.setToolTip("FloatingWordBall · 右键设置")
        self.menu = QMenu()
        self.wordbook_action = QAction("单词本…", self.menu)
        self.wordbook_action.triggered.connect(self.open_wordbook)
        self.menu.addAction(self.wordbook_action)
        self.my_books_action = QAction("我的单词本…", self.menu)
        self.my_books_action.triggered.connect(self.open_my_wordbooks)
        self.menu.addAction(self.my_books_action)
        self.calendar_action = QAction("学习日历…", self.menu)
        self.calendar_action.triggered.connect(self.open_calendar)
        self.menu.addAction(self.calendar_action)
        self.vocab_action = QAction("词库", self.menu)
        self.vocab_action.setEnabled(False)
        self.menu.addAction(self.vocab_action)
        self.vocab_menu = self.menu.addMenu("切换词库")
        self.vocab_actions = {}
        for name, title in (("CET4", "四级 CET4"), ("CET6", "六级 CET6"),
                            ("Kaoyan", "考研 Kaoyan"), ("TOEFL", "托福 TOEFL")):
            action = QAction(title, self.vocab_menu)
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, n=name: self.change_vocab(n))
            self.vocab_menu.addAction(action)
            self.vocab_actions[name] = action

        self.menu.addSeparator()
        self.opacity_menu = self.menu.addMenu("胶囊不透明度")
        for opacity in (0.5, 0.7, 0.9, 1.0):
            action = QAction(f"{round(opacity * 100)}%", self.opacity_menu)
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, o=opacity: self.change_opacity(o))
            self.opacity_menu.addAction(action)
            if abs(opacity - self.config["opacity"]) < 0.06:
                action.setChecked(True)

        self.theme_menu = self.menu.addMenu("颜色主题")
        self.theme_actions = {}
        for key, theme in THEMES.items():
            action = QAction(theme["label"], self.theme_menu)
            action.setCheckable(True)
            action.setChecked(key == self.config.get("theme", "green"))
            action.triggered.connect(lambda checked=False, name=key: self.change_theme(name))
            self.theme_menu.addAction(action)
            self.theme_actions[key] = action

        self.dock_menu = self.menu.addMenu("侧边隐藏")
        self.dock_actions = {}
        for key, label in ((None, "取消侧边隐藏"), ("left", "隐藏到左侧"), ("right", "隐藏到右侧")):
            action = QAction(label, self.dock_menu)
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, side=key: self.set_dock(side))
            self.dock_menu.addAction(action)
            self.dock_actions[key] = action
        self._update_dock_actions()

        self.menu.addSeparator()
        self.through_action = QAction("鼠标穿透  Alt+W", self.menu)
        self.through_action.setCheckable(True)
        self.through_action.triggered.connect(self.set_click_through)
        self.menu.addAction(self.through_action)
        self.recover_action = QAction("关闭鼠标穿透", self.menu)
        self.recover_action.triggered.connect(lambda: self.set_click_through(False))
        self.menu.addAction(self.recover_action)
        self.recover_action.setEnabled(False)
        self.restore_known_action = QAction("恢复今日已认识的单词", self.menu)
        self.restore_known_action.triggered.connect(self.restore_known_today)
        self.menu.addAction(self.restore_known_action)
        self.reset_action = QAction("重置胶囊大小与位置", self.menu)
        self.reset_action.triggered.connect(self.reset_capsule)
        self.menu.addAction(self.reset_action)
        self.next_action = QAction("立即换词", self.menu)
        self.next_action.triggered.connect(self.next_word)
        self.menu.addAction(self.next_action)
        self.boss_action = QAction("隐藏 / 显示  Alt+Q", self.menu)
        self.boss_action.triggered.connect(self.toggle_boss)
        self.menu.addAction(self.boss_action)
        self.menu.addSeparator()
        self.quit_action = QAction("退出", self.menu)
        self.quit_action.triggered.connect(self.quit)
        self.menu.addAction(self.quit_action)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _update_dock_actions(self):
        for key, action in self.dock_actions.items():
            action.setChecked(key == self.dock_side)

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            if self.config["click_through"]:
                self.set_click_through(False)
                self.tray.showMessage("鼠标穿透已关闭", "胶囊现在可以点击了。",
                                      QSystemTrayIcon.MessageIcon.Information, 3000)
            else:
                self.toggle_boss()

    def show_menu(self, position: QPoint):
        self.menu.popup(position)

    def _available_geometry(self, point: QPoint | None = None) -> QRect:
        if point is None:
            if self.isVisible():
                point = self.geometry().center()
            else:
                point = QCursor.pos()
        screen = (QGuiApplication.screenAt(point)
                  or QGuiApplication.screenAt(QCursor.pos())
                  or QGuiApplication.primaryScreen())
        return screen.availableGeometry()

    def _clamp_size(self, width: int | float, height: int | float):
        area = self._available_geometry()
        minimum_width, minimum_height = capsule_min_size(self.config["scale"])
        minimum_height = max(minimum_height,
                             self.ball.content_min_height_for_width(max(minimum_width, width)))
        maximum_width = max(minimum_width, min(MAX_CAPSULE_WIDTH, area.width()))
        maximum_height = max(minimum_height, min(MAX_CAPSULE_HEIGHT, area.height()))
        width = int(max(minimum_width, min(maximum_width, round(float(width)))))
        height = int(max(minimum_height, min(maximum_height, round(float(height)))))
        return width, height

    def _target_size(self):
        stored = self.config.get("size")
        if isinstance(stored, (list, tuple)) and len(stored) == 2:
            return int(stored[0]), int(stored[1])
        return default_capsule_size(self.config["scale"])

    def _apply_geometry(self, position=None, size=None):
        scale = self.config["scale"]
        raw = size if size is not None else self._target_size()
        width, height = self._clamp_size(raw[0], raw[1])
        self.expanded_size = (width, height)
        minimum_width, _ = capsule_min_size(scale)
        height = height if self.expanded else self.ball.collapsed_height()
        self.setMinimumSize(minimum_width,
                            self.ball.content_min_height_for_width(width) if self.expanded
                            else self.ball.collapsed_height())
        self.ball.set_expanded(self.expanded)
        self.resize(width, height)
        if position is None:
            stored = self.config.get("position")
            if isinstance(stored, (list, tuple)) and len(stored) == 2:
                position = stored
            else:
                area = self._available_geometry()
                y = area.top() + 100
                if self.dock_side == "left":
                    position = (area.left(), y)
                elif self.dock_side == "right":
                    position = (area.right() - self.width() + 1, y)
                else:
                    position = (area.right() - self.width() - 36, y)
        self.move(self._clamp_position(QPoint(round(position[0]), round(position[1]))))
        if self.dock_side:
            self._apply_dock()

    def _clamp_position(self, position: QPoint) -> QPoint:
        area = self._available_geometry(position)
        x = max(area.left(), min(position.x(), area.right() - self.width() + 1))
        y = max(area.top(), min(position.y(), area.bottom() - self.height() + 1))
        return QPoint(x, y)

    def _dock_area(self) -> QRect:
        if self.dock_area.isValid():
            return QRect(self.dock_area)
        area = self._available_geometry(self.geometry().center())
        self.dock_area = QRect(area)
        return area

    def _dock_strip(self) -> int:
        return max(4, min(EDGE_STRIP, max(4, self.width() // 4)))

    def _dock_offsets(self):
        area = self._dock_area()
        strip = self._dock_strip()
        if self.dock_side == "left":
            hidden = area.left() - self.width() + strip
            shown = area.left()
        else:
            hidden = area.right() - strip + 1
            shown = area.right() - self.width() + 1
        return hidden, shown

    def _apply_dock(self):
        if not self.dock_side:
            return
        area = self._dock_area()
        hidden, shown = self._dock_offsets()
        y = max(area.top(), min(self.y(), area.bottom() - self.height() + 1))
        self.move(hidden if self.dock_hidden else shown, y)

    def _edge_trigger_rect(self) -> QRect:
        if not self.dock_side:
            return QRect()
        area = self._dock_area()
        height = max(48, min(self.ball.collapsed_height(), area.height()))
        y = max(area.top(), min(self.y(), area.bottom() - height + 1))
        if self.dock_side == "left":
            return QRect(area.left(), y, EDGE_TRIGGER, height)
        return QRect(area.right() - EDGE_TRIGGER + 1, y, EDGE_TRIGGER, height)

    def _dock_reveal(self):
        if not self.dock_side:
            return
        self.edge_hide_timer.stop()
        self.dock_hidden = False
        self._apply_dock()
        self.raise_()

    def _schedule_edge_hide(self):
        if (not self.dock_side or self.dock_hidden or self.pinned or self.boss_hidden
                or self.config["click_through"] or self.dragging or self.resize_edge
                or self.book_menu_open or self.edge_hide_timer.isActive()):
            return
        self.edge_hide_timer.start()

    def _pointer_over_window(self, pointer: QPoint) -> bool:
        return self.geometry().contains(pointer)

    def _edge_hide_now(self, pointer: QPoint | None = None):
        if (not self.dock_side or self.dock_hidden or self.pinned or self.boss_hidden
                or self.config["click_through"] or self.dragging or self.resize_edge
                or self.book_menu_open):
            return
        pointer = pointer if pointer is not None else QCursor.pos()
        if self._pointer_over_window(pointer) or self._edge_trigger_rect().contains(pointer):
            return
        self.hovered = False
        self.ball.set_rating_hover("")
        self.ball.set_speaker_hover(False)
        self.set_expanded(False)
        self.dock_hidden = True
        self._apply_dock()

    def _edge_tick(self, pointer: QPoint | None = None):
        if (not self.dock_side or self.boss_hidden or self.config["click_through"]
                or self.dragging or self.resize_edge or self.book_menu_open):
            return
        pointer = pointer if pointer is not None else QCursor.pos()
        if self.pinned:
            if self.dock_hidden:
                self._dock_reveal()
            return
        if self.dock_hidden:
            if self._edge_trigger_rect().contains(pointer):
                self._dock_reveal()
                self._set_hovered(True)
            return
        if self._pointer_over_window(pointer):
            if not self.hovered:
                self._set_hovered(True)
            return
        self._schedule_edge_hide()

    def _edge_after_show(self):
        if (not self.dock_side or self.pinned or self.boss_hidden
                or self.config["click_through"]):
            return
        pointer = QCursor.pos()
        if self._edge_trigger_rect().contains(pointer):
            self._dock_reveal()
            self._set_hovered(True)
            return
        if not self._pointer_over_window(pointer):
            self.hovered = False
            self.ball.set_rating_hover("")
            self.ball.set_speaker_hover(False)
            self.set_expanded(False)
            self.dock_hidden = True
            self._apply_dock()

    def _snap_to_nearest(self):
        area = self._available_geometry(self.geometry().center())
        left_distance = abs(self.x() - area.left())
        right_distance = abs(area.right() - (self.x() + self.width() - 1))
        side = None
        if min(left_distance, right_distance) <= SNAP_DISTANCE:
            side = "left" if left_distance <= right_distance else "right"
        self.dock_side = side
        self.config["dock"] = side
        self.dock_hidden = False
        self.edge_hide_timer.stop()
        self.dock_area = self._available_geometry(self.geometry().center())
        if side:
            self._apply_dock()
        else:
            self.move(self._clamp_position(self.pos()))
        self._update_dock_actions()
        self.config["position"] = [self.x(), self.y()]
        self._save()

    def set_dock(self, side: str | None, persist: bool = True):
        if side not in ("left", "right"):
            side = None
        self.dock_side = side
        self.config["dock"] = side
        self.dock_hidden = False
        self.edge_hide_timer.stop()
        self.dock_area = self._available_geometry(self.geometry().center())
        if side:
            self._apply_dock()
        else:
            self.dock_area = QRect()
            self.move(self._clamp_position(self.pos()))
        self.config["position"] = [self.x(), self.y()]
        self._update_dock_actions()
        if persist:
            self._save()

    def set_expanded(self, expanded: bool, force: bool = False):
        if not expanded and self.pinned:
            return
        if expanded and self.dock_hidden:
            self._dock_reveal()
        if expanded == self.expanded and not force:
            return
        self.expanded = bool(expanded)
        self.ball.set_expanded(self.expanded)
        minimum_width, _ = capsule_min_size(self.config["scale"])
        if self.expanded:
            width = max(minimum_width, self.width())
            height = max(self.expanded_size[1],
                         self.ball.content_min_height_for_width(width))
            self.setMinimumSize(minimum_width,
                                self.ball.content_min_height_for_width(width))
            self.resize(width, height)
            area = self._available_geometry(self.geometry().center())
            if self.y() + height > area.bottom() + 1:
                self.move(self.x(), max(area.top(), area.bottom() - height + 1))
        else:
            width = max(minimum_width, self.width())
            height = self.ball.collapsed_height()
            self.setMinimumSize(minimum_width, height)
            self.resize(width, height)
        if self.dock_side:
            self._apply_dock()
        self._save_geometry()

    def _save_geometry(self):
        self.config["size"] = [self.expanded_size[0], self.expanded_size[1]]
        x = self.x()
        if self.dock_side:
            _, x = self._dock_offsets()
        self.config["position"] = [x, self.y()]

    def set_capsule_size(self, width: int, height: int, persist: bool = False):
        width, height = self._clamp_size(width, height)
        self.expanded_size = (width, height)
        minimum_width, _ = capsule_min_size(self.config["scale"])
        if self.expanded:
            required = self.ball.content_min_height_for_width(width)
            height = max(height, required)
            self.setMinimumSize(minimum_width, required)
            self.resize(width, height)
        else:
            height = self.ball.collapsed_height()
            self.setMinimumSize(minimum_width, height)
            self.resize(width, height)
        if self.dock_side:
            self._apply_dock()
        else:
            self.move(self._clamp_position(self.pos()))
        self._save_geometry()
        if persist:
            self._save()

    def _ensure_content_fits(self):
        if not self.expanded:
            return
        required = self.ball.content_min_height_for_width(self.width())
        if required <= self.height():
            return
        area = self._available_geometry(self.geometry().center())
        height = min(required, area.height())
        if height <= self.height():
            return
        self.expanded_size = (self.expanded_size[0], max(self.expanded_size[1], height))
        y = self.y()
        if y + height > area.bottom() + 1:
            y = max(area.top(), area.bottom() - height + 1)
        self.setMinimumSize(self.minimumWidth(), height)
        self.resize(self.width(), height)
        self.move(self.x(), y)
        if self.dock_side:
            self._apply_dock()
        self._save_geometry()

    def _edge_at(self, position: QPoint) -> str | None:
        margin = 8
        left = position.x() <= margin
        right = position.x() >= self.width() - margin - 1
        if not self.expanded:
            if left:
                return "left"
            if right:
                return "right"
            return None
        top = position.y() <= margin
        bottom = position.y() >= self.height() - margin - 1
        if top and left:
            return "topleft"
        if top and right:
            return "topright"
        if bottom and left:
            return "bottomleft"
        if bottom and right:
            return "bottomright"
        if left:
            return "left"
        if right:
            return "right"
        if top:
            return "top"
        if bottom:
            return "bottom"
        return None

    def _edge_cursor(self, edge: str):
        return {
            "left": Qt.CursorShape.SizeHorCursor,
            "right": Qt.CursorShape.SizeHorCursor,
            "top": Qt.CursorShape.SizeVerCursor,
            "bottom": Qt.CursorShape.SizeVerCursor,
            "topleft": Qt.CursorShape.SizeFDiagCursor,
            "bottomright": Qt.CursorShape.SizeFDiagCursor,
            "topright": Qt.CursorShape.SizeBDiagCursor,
            "bottomleft": Qt.CursorShape.SizeBDiagCursor,
        }.get(edge, Qt.CursorShape.OpenHandCursor)

    def _speaker_zone(self) -> QRectF:
        return self.ball.speaker_rect()

    def _rating_at(self, position: QPoint) -> str:
        point = QPointF(position)
        for key, rect in self.ball.button_rects().items():
            if rect.contains(point):
                return key
        return ""

    def _update_cursor(self, position: QPoint):
        rating = self._rating_at(position)
        speaker = self._speaker_zone().contains(QPointF(position))
        self.ball.set_rating_hover(rating)
        self.ball.set_speaker_hover(speaker)
        if rating or speaker:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            return
        self.setCursor(self._edge_cursor(self._edge_at(position)))

    def mousePressEvent(self, event):
        self.activateWindow()
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if event.button() == Qt.MouseButton.LeftButton:
            position = event.position().toPoint()
            rating = self._rating_at(position)
            if rating:
                self.rating_pressed = rating
                self.ball.set_rating_hover(rating)
                event.accept()
                return
            if self._speaker_zone().contains(event.position()):
                self.speak()
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                event.accept()
                return
            edge = self._edge_at(position)
            if edge:
                self.resize_edge = edge
                self.resize_origin_global = event.globalPosition().toPoint()
                self.resize_origin_rect = QRect(self.geometry())
                self.resize_moved = False
                self.setCursor(self._edge_cursor(edge))
                event.accept()
                return
            if self.press_global is None:
                self.pin_before_press = self.pinned
            self.drag_origin = event.globalPosition().toPoint() - self.pos()
            self.press_global = event.globalPosition().toPoint()
            self.drag_moved = False
            self.dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif event.button() == Qt.MouseButton.RightButton:
            self.show_menu(event.globalPosition().toPoint())
        event.accept()

    def mouseMoveEvent(self, event):
        if self.rating_pressed:
            hover = self._rating_at(event.position().toPoint())
            self.ball.set_rating_hover(hover)
            event.accept()
            return
        if self.resize_edge:
            self._resize_to(event.globalPosition().toPoint())
            event.accept()
            return
        if self.drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            target = event.globalPosition().toPoint() - self.drag_origin
            if self.press_global is not None and (event.globalPosition().toPoint()
                                                  - self.press_global).manhattanLength() > 3:
                self.drag_moved = True
            self.move(self._clamp_position(target))
            event.accept()
            return
        self._update_cursor(event.position().toPoint())
        super().mouseMoveEvent(event)

    def _resize_to(self, global_position: QPoint):
        if self.resize_origin_rect is None or self.resize_edge is None:
            return
        start = QRect(self.resize_origin_rect)
        delta = global_position - self.resize_origin_global
        area = self._available_geometry(global_position)
        minimum_width, _ = capsule_min_size(self.config["scale"])
        minimum_height = (self.ball.content_min_height_for_width(start.width())
                          if self.expanded else start.height())
        rect = QRect(start)
        if "left" in self.resize_edge:
            rect.setLeft(min(start.right() - minimum_width + 1,
                             max(area.left(), start.left() + delta.x())))
        if "right" in self.resize_edge:
            rect.setRight(max(start.left() + minimum_width - 1,
                              min(area.right(), start.right() + delta.x())))
        if self.expanded and "top" in self.resize_edge:
            rect.setTop(min(start.bottom() - minimum_height + 1,
                            max(area.top(), start.top() + delta.y())))
        if self.expanded and "bottom" in self.resize_edge:
            rect.setBottom(max(start.top() + minimum_height - 1,
                               min(area.bottom(), start.bottom() + delta.y())))
        if rect.width() < minimum_width:
            rect.setWidth(minimum_width)
        if self.expanded and rect.height() < minimum_height:
            rect.setHeight(minimum_height)
        if not self.expanded:
            rect.setHeight(start.height())
        self.resize_moved = rect != start
        self.setGeometry(rect)
        if self.dock_side:
            self._apply_dock()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.rating_pressed:
                pressed = self.rating_pressed
                self.rating_pressed = None
                self.ball.set_rating_hover("")
                if self._rating_at(event.position().toPoint()) == pressed:
                    if pressed == "add":
                        self.choose_wordbook_for_current()
                    else:
                        self.rate(pressed)
                event.accept()
                return
            resized = self.resize_edge is not None and self.resize_moved
            dragged = self.drag_origin is not None and self.drag_moved
            clicked = self.drag_origin is not None and not self.drag_moved
            self.resize_edge = None
            self.resize_origin_global = None
            self.resize_origin_rect = None
            self.drag_origin = None
            self.press_global = None
            self.dragging = False
            if resized:
                self._ensure_content_fits()
            if resized or dragged:
                self.expanded_size = (self.width(),
                                      self.expanded_size[1] if not self.expanded else self.height())
                self._snap_to_nearest()
                self._save_geometry()
                self._save()
            elif clicked:
                self.set_pinned(not self.pinned)
            self._update_cursor(event.position().toPoint())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            position = event.position().toPoint()
            if (not self._edge_at(position) and not self._speaker_zone().contains(event.position())
                    and not self._rating_at(position)):
                if self.pin_before_press is not None:
                    self.set_pinned(self.pin_before_press)
                    self.pin_before_press = None
                self.next_word()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event: QWheelEvent):
        delta = event.angleDelta().y()
        if not delta:
            event.ignore()
            return
        if delta > 0:
            self.previous_word()
        else:
            self.next_word()
        event.accept()

    def enterEvent(self, event):
        self._set_hovered(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        if not self.dragging and self.resize_edge is None:
            self._set_hovered(False)
            self._schedule_edge_hide()
        super().leaveEvent(event)

    def _set_hovered(self, hovered: bool):
        if self.boss_hidden or self.config["click_through"]:
            return
        self.hovered = bool(hovered)
        if self.hovered:
            if self._ensure_today():
                self.show_current()
            self.set_expanded(True)
        else:
            self.ball.set_rating_hover("")
            self.ball.set_speaker_hover(False)
            if not self.pinned and not self.book_menu_open:
                self.set_expanded(False)

    def set_pinned(self, pinned: bool):
        self.pinned = bool(pinned)
        self.ball.set_pinned(self.pinned)
        if self.pinned:
            self.set_expanded(True)
        else:
            self.set_expanded(self.hovered)

    def _empty_message(self) -> str:
        study = self.config["study"]
        if study.get("mode") == "book":
            name = study.get("book", "")
            if not self.wordbooks.get(name):
                return "这个单词本还没有词。背单词时点「不认识」右侧的立方体加号，就能把当前词收进来。"
            return "这本单词本今天的内容已经背完。点「单词本…」换一组，或等明天自动重置。"
        if study.get("known_today"):
            return "今天标记为认识的词已经收好，明天会自动回到循环。模糊和不认识的词仍会继续出现。"
        return "今天的词已经背完。点「单词本…」换一组，或等明天自动重置。"

    def _request_detail(self, word: str):
        if self.detail_fetcher is None or not word:
            return
        key = str(word).lower()
        if key in self.detail_cache or key in self.detail_misses:
            return
        self.detail_fetcher.request(word)

    def _apply_detail(self, word: str, detail: dict):
        key = str(word).lower()
        if not detail:
            self.detail_misses.add(key)
            return
        self.detail_misses.discard(key)
        self.detail_cache[key] = detail
        if len(self.detail_cache) > 900:
            for stale in list(self.detail_cache)[:-900]:
                self.detail_cache.pop(stale, None)
        save_detail_cache(self.detail_cache_path, self.detail_cache)
        if (self.current_entry
                and str(self.current_entry.get("word", "")).lower() == key):
            self.ball.set_detail(detail)
            if self.expanded:
                self._ensure_content_fits()

    def show_current(self):
        self._ensure_today()
        if not self.deck:
            self.current_entry = None
            self.setToolTip(self.base_tooltip)
            self.ball.set_content({"word": "今日完成", "translation": self._empty_message()})
            self.ball.set_pinned(self.pinned)
            if self.expanded:
                self._ensure_content_fits()
            return
        self.current_index = self.deck_index % len(self.deck)
        self.current_entry = self.deck[self.current_index]
        word = self.current_entry.get("word", "")
        detail = self.detail_cache.get(str(word).lower())
        stars = stars_for(word, self.importance)
        self.setToolTip(f"{star_label(stars)} {'★' * stars}{'☆' * (3 - stars)} · {self.base_tooltip}")
        self.ball.set_content(self.current_entry, detail=detail, stars=stars)
        self.ball.set_pinned(self.pinned)
        if self.fetch_details and not detail:
            self._request_detail(word)
        if self.expanded:
            self._ensure_content_fits()

    def next_word(self):
        if self._ensure_today():
            self.show_current()
            self._save()
            return
        if not self.deck:
            return
        self.deck_index = (self.deck_index + 1) % len(self.deck)
        self.show_current()
        self._save()

    def previous_word(self):
        if self._ensure_today():
            self.show_current()
            self._save()
            return
        if not self.deck:
            return
        self.deck_index = (self.deck_index - 1) % len(self.deck)
        self.show_current()
        self._save()

    def rate(self, rating: str):
        if rating not in ("known", "fuzzy", "unknown"):
            return
        if self._ensure_today():
            self.show_current()
            self._save()
            return
        if not self.deck or self.current_entry is None:
            return
        word = self.current_entry.get("word", "")
        study = self.config["study"]
        study.setdefault("ratings", {})[word] = rating
        counts = study.setdefault("counts", {"known": 0, "fuzzy": 0, "unknown": 0})
        counts[rating] = int(counts.get(rating, 0)) + 1
        day_counts = self._history_counts()
        day_counts[rating] = int(day_counts.get(rating, 0)) + 1
        if rating == "known":
            known = study.setdefault("known_today", [])
            if word and word not in known:
                known.append(word)
        if rating == "unknown":
            self._add_entry_to_book(AUTO_WORD_BOOK, self.current_entry)
        index = self.current_index
        entry = self.deck.pop(index)
        remaining = len(self.deck)
        next_index = index if index < remaining else 0
        if rating != "known":
            if rating == "fuzzy":
                insert_at = min(len(self.deck), index + 2)
            else:
                insert_at = min(len(self.deck), index + 1)
            self.deck.insert(insert_at, entry)
        self.deck_index = next_index
        self.show_current()
        self._save()

    def _add_entry_to_book(self, name: str, entry: dict | None, notify: bool = False) -> bool:
        clean = _wordbook_entry(entry or {})
        if clean is None or not name:
            return False
        clean["vocabulary"] = (entry or {}).get("vocabulary") or self.config["vocabulary"]
        book = self.wordbooks.setdefault(name, [])
        if any(item.get("word") == clean["word"] for item in book):
            if notify:
                QToolTip.showText(QCursor.pos(), f"「{clean['word']}」已经在「{name}」里了", self)
            return False
        book.append(clean)
        save_wordbooks(self.wordbooks_path, self.wordbooks)
        if notify:
            QToolTip.showText(QCursor.pos(), f"已把「{clean['word']}」加入「{name}」", self)
        return True

    def add_current_to_book(self, name: str, notify: bool = True) -> bool:
        if self.current_entry is None:
            return False
        return self._add_entry_to_book(name, self.current_entry, notify)

    def _create_book_for_current(self):
        name, ok = prompt_text(self, "新建单词本", "给这本单词本起个名字：")
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        if name not in self.wordbooks:
            self.wordbooks[name] = []
            save_wordbooks(self.wordbooks_path, self.wordbooks)
        self.add_current_to_book(name)

    def _build_wordbook_menu(self):
        menu = QMenu(self)
        for name in [AUTO_WORD_BOOK] + [key for key in self.wordbooks if key != AUTO_WORD_BOOK]:
            action = menu.addAction(f"{name}    · {len(self.wordbooks.get(name, []))} 词")
            action.triggered.connect(
                lambda checked=False, book=name: self.add_current_to_book(book))
        menu.addSeparator()
        action = menu.addAction("新建单词本…")
        action.triggered.connect(self._create_book_for_current)
        return menu

    def choose_wordbook_for_current(self):
        if self.config["click_through"] or self.current_entry is None:
            return
        self.book_menu_open = True
        try:
            menu = self._build_wordbook_menu()
            rect = self.ball.button_rects().get("add")
            position = (self.mapToGlobal(rect.bottomLeft().toPoint())
                        if rect is not None else QCursor.pos())
            menu.exec(position)
        finally:
            self.book_menu_open = False
            if not self.pinned and not self.rect().contains(
                    self.mapFromGlobal(QCursor.pos())):
                self._set_hovered(False)

    def open_my_wordbooks(self):
        if self.config["click_through"]:
            return
        dialog = MyWordbooksDialog(copy.deepcopy(self.wordbooks), self.palette, self)
        accepted = present_dialog(dialog) == QDialog.DialogCode.Accepted
        if dialog.changed:
            self.wordbooks = dialog.wordbooks
            save_wordbooks(self.wordbooks_path, self.wordbooks)
            if self.config["study"].get("mode") == "book":
                self._rebuild_deck()
                self.show_current()
        if accepted and dialog.result_book:
            self.apply_wordbook(dialog.result_book)
        else:
            self._save()

    def open_calendar(self):
        if self.config["click_through"]:
            return
        present_dialog(StudyCalendarDialog(self.config["study"].get("history", {}), self.palette, self))

    def open_wordbook(self):
        if self.config["click_through"]:
            return
        dialog = WordbookDialog(self.vocab_dir, self.config["vocabulary"],
                                self.config["study"], self.palette, self)
        if present_dialog(dialog) == QDialog.DialogCode.Accepted:
            self.apply_study_plan(dialog.result_mode, dialog.result_start,
                                  dialog.result_count, dialog.result_custom,
                                  dialog.result_vocab)

    def apply_study_plan(self, mode: str, start_index: int, daily_count: int,
                         custom_words: list, vocabulary: str | None = None):
        self._ensure_today()
        if vocabulary and vocabulary != self.config["vocabulary"]:
            self._load_vocab(vocabulary)
        study = self.config["study"]
        study["mode"] = "custom" if mode == "custom" else "sequence"
        study["start_index"] = max(0, int(start_index))
        study["daily_count"] = max(1, min(1000, int(daily_count)))
        study["custom_words"] = list(custom_words)
        study["book"] = ""
        study["cursor"] = 0
        study["date"] = date.today().isoformat()
        self._rebuild_deck()
        self.show_current()
        self._save()

    def apply_wordbook(self, name: str):
        if name not in self.wordbooks:
            return
        self._ensure_today()
        study = self.config["study"]
        study["mode"] = "book"
        study["book"] = name
        study["start_index"] = 0
        study["custom_words"] = []
        study["daily_count"] = max(1, min(1000, len(self.wordbooks.get(name, []))))
        study["cursor"] = 0
        study["date"] = date.today().isoformat()
        self._rebuild_deck()
        self.show_current()
        self._save()

    def change_opacity(self, opacity: float):
        self.config["opacity"] = opacity
        self._update_opacity()
        for action in self.opacity_menu.actions():
            action.setChecked(action.text() == f"{round(opacity * 100)}%")
        self._save()

    def change_theme(self, name: str):
        if name not in THEMES:
            return
        self.config["theme"] = name
        self.palette = theme_palette(name)
        for key, action in self.theme_actions.items():
            action.setChecked(key == name)
        self.ball.set_palette(self.palette)
        icon = app_icon(self.palette)
        self.setWindowIcon(icon)
        self.tray.setIcon(icon)
        app = QApplication.instance()
        if app is not None:
            app.setWindowIcon(icon)
        self._save()

    def change_vocab(self, name: str):
        try:
            self._ensure_today()
            self._load_vocab(name)
            for key, action in self.vocab_actions.items():
                action.setChecked(key == name)
            study = self.config["study"]
            study["mode"] = "sequence"
            study["book"] = ""
            study["start_index"] = 0
            study["custom_words"] = []
            study["cursor"] = 0
            self._rebuild_deck()
            self.show_current()
            self._save()
        except RuntimeError as exc:
            self.tray.showMessage("词库无法打开", str(exc), QSystemTrayIcon.MessageIcon.Warning, 5000)

    def _update_opacity(self):
        effective = self.config["opacity"] * (0.45 / 0.92 if self.config["click_through"] else 1.0)
        self.opacity_effect.setOpacity(max(0.2, effective))

    def restore_known_today(self):
        study = self.config["study"]
        known = list(study.get("known_today", []))
        if not known:
            self.tray.showMessage("今天还没有隐藏的词", "标记为认识的词会出现在这里。",
                                  QSystemTrayIcon.MessageIcon.Information, 3000)
            return
        study["known_today"] = []
        self._rebuild_deck()
        self.show_current()
        self._save()
        self.tray.showMessage("已恢复今日单词", f"{len(known)} 个标记为认识的词回到了循环。",
                              QSystemTrayIcon.MessageIcon.Information, 3500)

    def reset_capsule(self):
        self.dock_side = None
        self.dock_hidden = False
        self.dock_area = QRect()
        self.edge_hide_timer.stop()
        self.config["dock"] = None
        self.config["position"] = None
        self.config["size"] = None
        self.expanded_size = default_capsule_size(self.config["scale"])
        self._apply_geometry(None)
        if self.expanded:
            self._ensure_content_fits()
        self._update_dock_actions()
        self._save()
        self.tray.showMessage("胶囊已复位", "大小、位置和吸附都已回到默认。",
                              QSystemTrayIcon.MessageIcon.Information, 3000)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if self.config["click_through"]:
                self.set_click_through(False)
            elif self.pinned:
                self.set_pinned(False)
                self.set_expanded(False)
            elif self.expanded:
                self.hovered = False
                self.set_expanded(False)
            else:
                super().keyPressEvent(event)
                return
            event.accept()
            return
        super().keyPressEvent(event)

    def set_click_through(self, enabled: bool, persist: bool = True):
        enabled = bool(enabled)
        self.config["click_through"] = enabled
        self.through_action.setChecked(enabled)
        self.recover_action.setEnabled(enabled)
        self.through_action.setText("鼠标穿透（已开启）  Alt+W" if enabled else "鼠标穿透  Alt+W")
        self._update_opacity()
        if enabled:
            self.hovered = False
            self.ball.set_rating_hover("")
            self.ball.set_speaker_hover(False)
            if not self.pinned:
                self.set_expanded(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, enabled)
        if sys.platform == "win32":
            user32 = ctypes.windll.user32
            style = user32.GetWindowLongW(int(self.winId()), -20)
            user32.SetWindowLongW(int(self.winId()), -20, style | 0x00000020 if enabled else style & ~0x00000020)
        if not enabled and not self.boss_hidden and self.isVisible():
            pointer = self.mapFromGlobal(QCursor.pos())
            if self.rect().contains(pointer):
                self.hovered = True
                self.set_expanded(True)
                self._update_cursor(pointer)
            elif self.dock_side:
                self._dock_reveal()
        if persist:
            self._save()
        if enabled and persist:
            self.tray.showMessage(
                "鼠标穿透已开启",
                "托盘图标双击、托盘菜单「关闭鼠标穿透」或 Alt+W 都可以关闭；重启后会自动关闭。",
                QSystemTrayIcon.MessageIcon.Information, 6000)

    def _hotkey(self, action: str):
        if action == "boss":
            self.toggle_boss()
        else:
            self.set_click_through(not self.config["click_through"])

    def toggle_boss(self):
        self.boss_hidden = not self.boss_hidden
        if self.boss_hidden:
            self.hovered = False
            self.ball.set_rating_hover("")
            self.hide()
        else:
            self.show()
            self.raise_()
            if not self.config["click_through"] and self.rect().contains(
                    self.mapFromGlobal(QCursor.pos())):
                self.hovered = True
                self.set_expanded(True)
            elif self.pinned:
                self.set_expanded(True)
            else:
                self.hovered = False
                self.set_expanded(False)
            self._edge_after_show()

    def speak(self):
        if self.current_entry and not self.config["click_through"]:
            self.speech.speak(self.current_entry["word"])

    def start(self):
        self.show()
        self.raise_()
        if not self.config["click_through"] and self.rect().contains(
                self.mapFromGlobal(QCursor.pos())):
            self.hovered = True
            self.set_expanded(True)
        elif self.pinned:
            self.set_expanded(True)
        else:
            self.hovered = False
            self.set_expanded(False)
        self._edge_after_show()

    def quit(self):
        if self.quitting:
            return
        self.quitting = True
        if getattr(self, "day_timer", None) is not None:
            self.day_timer.stop()
        if getattr(self, "edge_timer", None) is not None:
            self.edge_timer.stop()
        if getattr(self, "edge_hide_timer", None) is not None:
            self.edge_hide_timer.stop()
        self.hotkeys.stop()
        if self.detail_fetcher is not None:
            self.detail_fetcher.stop()
        self.tray.hide()
        self.speech.shutdown()
        self._save()
        QApplication.quit()

    def closeEvent(self, event):
        if not self.quitting:
            event.ignore()
            self.toggle_boss()
        else:
            super().closeEvent(event)

def main(argv=None):
    parser = argparse.ArgumentParser(description="桌面悬浮背单词工具")
    parser.add_argument("--offline-init", action="store_true", help="启动前用内置词库补齐缺失文件")
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="用户数据目录（默认安装版为 AppData，便携版为 EXE 所在目录）")
    parser.add_argument("--portable", action="store_true", help="强制使用便携模式（数据写在 EXE 旁）")
    parser.add_argument("--config", type=Path, default=None, help="指定 config.json 路径")
    parser.add_argument("--vocab-dir", type=Path, default=None, help="指定词库目录")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    args = parser.parse_args(argv)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setQuitOnLastWindowClosed(False)
    data_dir = None
    log_path = None
    try:
        legacy_dir = (Path(sys.executable).resolve().parent
                      if _is_frozen() and not args.portable else None)
        data_dir = prepare_data_dir(
            args.data_dir or default_data_dir(args.portable), legacy_dir=legacy_dir)
        log_path = configure_logging(data_dir)
        config_path = args.config.expanduser() if args.config else data_dir / "config.json"
        if args.vocab_dir:
            vocab_dir = args.vocab_dir.expanduser()
            ensure_local_vocab(vocab_dir)
        else:
            vocab_dir = prepare_vocab_dir(data_dir)
        config = load_config(config_path)
        app.setWindowIcon(app_icon(theme_palette(config["theme"])))
        window = FloatingWindow(config, vocab_dir, config_path)
    except (OSError, RuntimeError) as exc:
        LOGGER.exception("FloatingWordBall startup failed")
        QMessageBox.critical(
            None, "FloatingWordBall 无法启动",
            f"无法初始化 FloatingWordBall。\n\n{exc}\n\n"
            f"数据目录：{data_dir if data_dir is not None else '未创建'}\n"
            f"日志：{log_path if log_path is not None else '不可用'}\n\n"
            "便携版请解压到有写入权限的文件夹后重新运行。")
        return 1
    app.aboutToQuit.connect(window.quit)
    window.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
