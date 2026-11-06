#!/usr/bin/env python3
"""Render real FloatingWordBall Qt widgets into website-ready PNG screenshots.

Run from the repository root:
    py -3.11 -B packaging/capture_screenshots.py
"""
from __future__ import annotations

import copy
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
os.environ.setdefault(
    "QT_QPA_FONTDIR",
    str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"),
)

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import main as appmod  # noqa: E402

OUT = Path(__file__).resolve().parent / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)


def pump(app: QApplication, rounds: int = 12) -> None:
    for _ in range(rounds):
        app.processEvents()


def save_widget(app, widget, filename: str, colors=("#E8F2E4", "#F8FBF4"),
                minimum=(1100, 620), padding: int = 56) -> Path:
    widget.show()
    widget.raise_()
    pump(app)
    pix = widget.grab()
    if pix.isNull():
        raise RuntimeError(f"grab() returned an empty pixmap for {filename}")

    width = max(minimum[0], pix.width() + padding * 2)
    height = max(minimum[1], pix.height() + padding * 2)
    canvas = QImage(width, height, QImage.Format.Format_ARGB32)
    canvas.fill(QColor(colors[0]))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    gradient = QLinearGradient(0, 0, width, height)
    gradient.setColorAt(0.0, QColor(colors[0]))
    gradient.setColorAt(1.0, QColor(colors[1]))
    painter.fillRect(canvas.rect(), gradient)
    painter.setPen(Qt.PenStyle.NoPen)
    for cx, cy, radius, alpha in (
        (int(width * 0.12), int(height * 0.18), int(width * 0.22), 26),
        (int(width * 0.88), int(height * 0.82), int(width * 0.28), 18),
        (int(width * 0.70), int(height * 0.12), int(width * 0.10), 22),
    ):
        painter.setBrush(QColor(255, 255, 255, alpha))
        painter.drawEllipse(cx - radius, cy - radius, radius * 2, radius * 2)

    x = (width - pix.width()) // 2
    y = (height - pix.height()) // 2
    painter.setBrush(QColor(10, 30, 24, 24))
    painter.drawRoundedRect(QRectF(x + 10, y + 14, pix.width(), pix.height()), 24, 24)
    painter.drawPixmap(int(x), int(y), pix)
    painter.end()

    target = OUT / filename
    if not canvas.save(str(target), "PNG"):
        raise RuntimeError(f"could not save {target}")
    print(f"wrote {target.relative_to(ROOT)} ({pix.width()}x{pix.height()} UI)")
    return target


def make_window(app, theme: str, scale: float, size):
    data_dir = Path(tempfile.mkdtemp(prefix="fwb-shot-"))
    config = copy.deepcopy(appmod.DEFAULTS)
    config["theme"] = theme
    config["scale"] = scale
    config["size"] = list(size)
    config["dock"] = None
    config["position"] = [40, 40]
    config["study"]["date"] = appmod.date.today().isoformat()
    vocab_dir = appmod.prepare_vocab_dir(data_dir)
    window = appmod.FloatingWindow(config, vocab_dir, data_dir / "config.json", fetch_details=False)
    window.dock_side = None
    return window, data_dir, vocab_dir


def sample_entry(vocab_dir: Path):
    entries = appmod.load_vocab(vocab_dir / "CET4.json")
    entry = next((item for item in entries if item.get("word") == "abandon"), entries[0])
    entry = dict(entry)
    entry["phrases"] = [
        {"phrase": "abandon oneself to", "translation": "沉溺于；纵情于"},
        {"phrase": "abandon ship", "translation": "弃船"},
    ]
    detail = {
        "senses": ["vt. 放弃；抛弃；遗弃", "n. 放任；无拘束"],
        "forms": ["abandoned", "abandoning", "abandonment"],
    }
    return entry, detail


def run() -> int:
    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setApplicationName("FloatingWordBall-screenshots")
    temporary_dirs = []

    window, data_dir, vocab_dir = make_window(app, "green", 1.08, (560, 300))
    temporary_dirs.append(data_dir)
    entry, detail = sample_entry(vocab_dir)
    window.ball.set_content(entry, detail=detail, stars=3)
    window.set_capsule_size(560, 300)
    window.set_expanded(True, force=True)
    pump(app, 20)
    save_widget(app, window, "01-expanded-green.png",
                ("#E7F1E2", "#FAFCF5"), minimum=(1080, 600))
    window.hide()
    window.deleteLater()
    pump(app)

    window2, data_dir2, vocab_dir2 = make_window(app, "blue", 1.4, (560, 200))
    temporary_dirs.append(data_dir2)
    entry2, detail2 = sample_entry(vocab_dir2)
    window2.ball.set_content(entry2, detail=detail2, stars=3)
    window2.set_capsule_size(560, 190)
    window2.set_expanded(False, force=True)
    pump(app, 20)
    save_widget(app, window2, "02-collapsed-blue.png",
                ("#E5F0FA", "#F8FCFF"), minimum=(1080, 420))
    window2.hide()
    window2.deleteLater()
    pump(app)

    palette = appmod.theme_palette("green")
    study = copy.deepcopy(appmod.DEFAULTS["study"])
    study["daily_count"] = 30
    wordbook = appmod.WordbookDialog(vocab_dir, "CET4", study, palette)
    wordbook.tabs.setCurrentIndex(0)
    pump(app, 20)
    save_widget(app, wordbook, "03-wordbook-picker.png",
                ("#E9F1E5", "#FBFDF8"), minimum=(1000, 820), padding=48)
    wordbook.close()
    wordbook.deleteLater()
    pump(app)

    entries = appmod.load_vocab(vocab_dir / "CET4.json")
    books = {
        appmod.AUTO_WORD_BOOK: [dict(item) for item in entries[:7]],
        "写作高频": [dict(item) for item in entries[20:26]],
    }
    my_books = appmod.MyWordbooksDialog(books, palette)
    my_books.books_list.setCurrentRow(0)
    pump(app, 20)
    save_widget(app, my_books, "04-my-wordbooks.png",
                ("#EEF3EA", "#FCFDF9"), minimum=(1080, 780), padding=48)
    my_books.close()
    my_books.deleteLater()
    pump(app)

    for directory in temporary_dirs:
        shutil.rmtree(directory, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
