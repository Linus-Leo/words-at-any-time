import copy
import json
import tempfile
from datetime import date
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QEnterEvent, QFont, QFontMetrics, QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog

import main
from vocab_fetcher import (
    fallback_words, load_vocab, normalize_entry, normalize_vocab, write_json_atomic,
)


class VocabularyTests(unittest.TestCase):
    def test_normalizes_source_fields_and_missing_phonetic(self):
        entry = normalize_entry({
            "word": "  accomplish ", "translations": [{"type": "v.", "translation": "完成"}],
            "phrases": [{"phrase": "accomplish a goal", "translation": "实现目标"}],
        })
        self.assertEqual(entry["word"], "accomplish")
        self.assertEqual(entry["translation"], "v. 完成")
        self.assertEqual(entry["phonetic"], "")
        self.assertEqual(entry["phrases"][0]["phrase"], "accomplish a goal")

    def test_invalid_vocab_does_not_replace_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vocab.json"
            write_json_atomic(path, [{"word": "retain", "translation": "保留"}])
            with patch("vocab_fetcher.json.dump", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    write_json_atomic(path, [{"word": "replace", "translation": "替换"}])
            self.assertEqual(load_vocab(path)[0]["word"], "retain")

    def test_fallback_has_entries_and_valid_shape(self):
        entries = fallback_words()
        self.assertGreaterEqual(len(entries), 40)
        self.assertTrue(all(entry["word"] and entry["translation"] for entry in entries))
        self.assertEqual(normalize_vocab(entries), entries)


class ConfigAndStudyTests(unittest.TestCase):
    def test_config_clamps_untrusted_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"scale": 99, "opacity": -1, "theme": "unknown",
                                        "vocabulary": "unknown", "position": [1, "bad"]}),
                            encoding="utf-8")
            config = main.load_config(path)
            self.assertEqual(config["scale"], main.MAX_SCALE)
            self.assertEqual(config["opacity"], 0.25)
            self.assertEqual(config["theme"], "green")
            self.assertEqual(config["vocabulary"], "CET4")
            self.assertIsNone(config["position"])
            self.assertEqual(config["study"]["mode"], "sequence")

    def test_study_plan_is_clamped_and_kept_nested(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"study": {
                "mode": "nonsense", "start_index": -3, "daily_count": 9999,
                "custom_words": ["apple", 3, "apple"], "counts": {"known": -2},
                "ratings": {"keep": "known", "drop": "nonsense"},
            }}), encoding="utf-8")
            config = main.load_config(path)
            self.assertEqual(config["study"]["mode"], "sequence")
            self.assertEqual(config["study"]["start_index"], 0)
            self.assertEqual(config["study"]["daily_count"], 1000)
            self.assertEqual(config["study"]["custom_words"], ["apple"])
            self.assertEqual(config["study"]["counts"]["known"], 0)
            self.assertEqual(config["study"]["ratings"], {"keep": "known"})


class DetailAndImportanceTests(unittest.TestCase):
    def test_stars_follow_frequency_thresholds(self):
        ranks = {"core": 1, "common": 5000, "rare": 20000}
        self.assertEqual(main.stars_for("core", ranks), 3)
        self.assertEqual(main.stars_for("common", ranks), 2)
        self.assertEqual(main.stars_for("rare", ranks), 1)
        self.assertEqual(main.stars_for("missing", ranks), 1)
        self.assertEqual(main.stars_for("CORE", ranks), 3)

    def test_youdao_payload_becomes_senses_forms_and_examples(self):
        payload = {
            "ec": {"word": [{
                "trs": [
                    {"tr": [{"l": {"i": "adv. 突然地；<b>唐突地</b>"}}]},
                    {"tr": [{"l": {"i": ["adj. 突然的"]}}]},
                ],
                "wfs": [{"wf": {"name": "比较级", "value": "more abrupt"}}],
            }]},
            "blng_sents_part": {"sentence-pair": [{
                "sentence": "The sky darkened abruptly.",
                "sentence-translation": "天突然变暗了。",
            }]},
        }
        detail = main.parse_youdao_detail("abruptly", payload)
        self.assertEqual(detail["senses"], ["adv. 突然地；唐突地", "adj. 突然的"])
        self.assertEqual(detail["forms"], ["比较级 more abrupt"])
        self.assertEqual(detail["examples"][0]["zh"], "天突然变暗了。")
        self.assertEqual(main.parse_youdao_detail("none", {}), {})

    def test_detail_html_keeps_word_card_height(self):
        app = QApplication.instance() or QApplication([])
        ball = main.BallWidget(1.0)
        ball.resize(380, 300)
        ball.set_expanded(True)
        ball.set_content(
            {"word": "appropriate", "translation": "adj. 适当的"},
            detail={
                "senses": ["adj. 适当的；恰当的；合适的", "v. 占用；拨出"],
                "forms": ["过去式 appropriated", "现在分词 appropriating"],
                "examples": [{"en": "These clothes are appropriate.",
                              "zh": "这些衣服很合适。"}],
            }, stars=3)
        app.processEvents()
        label = ball.translation
        self.assertEqual(ball.stars, 3)
        self.assertNotIn("These clothes are appropriate.", label.text())
        self.assertIn("变形", label.text())
        self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
        self.assertGreaterEqual(ball.content_min_height_for_width(380),
                                label.heightForWidth(label.width()))


    def test_detail_html_shows_four_senses_and_two_phrases(self):
        app = QApplication.instance() or QApplication([])
        ball = main.BallWidget(1.0)
        ball.resize(380, 320)
        ball.set_expanded(True)
        ball.set_content(
            {"word": "apple", "translation": "n. 苹果",
             "phrases": [{"phrase": "apple pie", "translation": "苹果派"},
                          {"phrase": "apple tree", "translation": "苹果树"}]},
            detail={"senses": ["n. 苹果", "n. 苹果树", "n. 苹果公司", "n. 宝贝"]},
            stars=2)
        app.processEvents()
        text = ball.translation.text()
        self.assertIn("n. 宝贝", text)
        self.assertIn("apple tree", text)
        self.assertGreaterEqual(ball.translation.height(),
                                ball.translation.heightForWidth(ball.translation.width()))

class FloatingWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        main.ensure_local_vocab(self.directory)
        self.config_path = self.directory / "config.json"
        self.window = main.FloatingWindow(
            main.load_config(self.config_path), self.directory, self.config_path,
            fetch_details=False)
        self.window.move(500, 500)
        self.window.hotkeys.stop()
        self.window.edge_timer.stop()

    def tearDown(self):
        self.window.hotkeys.stop()
        self.window.edge_timer.stop()
        self.window.edge_hide_timer.stop()
        if self.window.detail_fetcher is not None:
            self.window.detail_fetcher.stop()
        self.window.tray.hide()
        self.window.speech.shutdown()
        self.window.hide()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def _show(self):
        self.window.show()
        self.window.start()
        self.app.processEvents()

    def test_legacy_study_config_backfills_today_and_history(self):
        legacy = main.study_config({
            "date": "2026-10-01",
            "counts": {"known": 2, "fuzzy": 1, "unknown": 3},
            "ratings": {"alpha": "known", "beta": "fuzzy", "gamma": "known"},
        })
        self.assertEqual(legacy["known_today"], ["alpha", "gamma"])
        self.assertEqual(legacy["history"]["2026-10-01"],
                         {"known": 2, "fuzzy": 1, "unknown": 3})
        explicit = main.study_config({
            "date": "2026-10-02", "known_today": [],
            "history": {"2026-10-01": {"known": 1, "fuzzy": 0, "unknown": 0}},
            "counts": {"known": 0, "fuzzy": 0, "unknown": 0},
            "ratings": {"alpha": "known"},
        })
        self.assertEqual(explicit["known_today"], [])

    def test_hover_expands_and_leaving_collapses(self):
        self._show()
        self.window._set_hovered(True)
        self.assertTrue(self.window.expanded)
        self.assertGreaterEqual(self.window.height(), self.window.ball.content_min_height_for_width(
            self.window.width()))
        self.window._set_hovered(False)
        self.assertFalse(self.window.expanded)
        self.assertEqual(self.window.height(), self.window.ball.collapsed_height())

    def test_enter_and_leave_events_drive_expansion(self):
        self._show()
        enter = QEnterEvent(QPointF(100, 25), QPointF(100, 25), QPointF(600, 525))
        QApplication.sendEvent(self.window, enter)
        self.app.processEvents()
        self.assertTrue(self.window.expanded)
        QApplication.sendEvent(self.window, QEvent(QEvent.Type.Leave))
        self.app.processEvents()
        self.assertFalse(self.window.expanded)

    def test_click_pins_and_second_click_unpins(self):
        self._show()
        QTest.mouseClick(self.window, Qt.MouseButton.LeftButton, pos=QPoint(100, 25))
        self.app.processEvents()
        self.assertTrue(self.window.pinned)
        self.assertTrue(self.window.expanded)
        QTest.mouseClick(self.window, Qt.MouseButton.LeftButton, pos=QPoint(100, 25))
        self.app.processEvents()
        self.assertFalse(self.window.pinned)
        self.assertFalse(self.window.expanded)

    def test_double_click_changes_word_without_toggling_pin(self):
        self._show()
        self.window._set_hovered(True)
        first = self.window.current_entry["word"]
        QTest.mouseDClick(self.window, Qt.MouseButton.LeftButton, pos=QPoint(100, 25))
        self.app.processEvents()
        self.assertFalse(self.window.pinned)
        self.assertNotEqual(self.window.current_entry["word"], first)

    def test_double_click_keeps_existing_pin_state(self):
        self._show()
        self.window._set_hovered(True)
        self.window.set_pinned(True)
        first = self.window.current_entry["word"]
        QTest.mouseDClick(self.window, Qt.MouseButton.LeftButton, pos=QPoint(100, 25))
        self.app.processEvents()
        self.assertTrue(self.window.pinned)
        self.assertNotEqual(self.window.current_entry["word"], first)

    def test_pinned_word_stays_open_when_pointer_leaves(self):
        self._show()
        QTest.mouseClick(self.window, Qt.MouseButton.LeftButton, pos=QPoint(100, 25))
        self.app.processEvents()
        self.assertTrue(self.window.pinned)
        self.window._set_hovered(False)
        self.assertTrue(self.window.expanded)

    def test_wheel_switches_words_and_ctrl_wheel_does_not_zoom(self):
        self._show()
        first = self.window.current_entry["word"]
        down = QWheelEvent(QPointF(40, 30), QPointF(540, 530), QPoint(),
                           QPoint(0, -120), Qt.MouseButton.NoButton,
                           Qt.KeyboardModifier.NoModifier,
                           Qt.ScrollPhase.NoScrollPhase, False)
        self.window.wheelEvent(down)
        second = self.window.current_entry["word"]
        self.assertNotEqual(first, second)
        up = QWheelEvent(QPointF(40, 30), QPointF(540, 530), QPoint(),
                         QPoint(0, 120), Qt.MouseButton.NoButton,
                         Qt.KeyboardModifier.NoModifier,
                         Qt.ScrollPhase.NoScrollPhase, False)
        self.window.wheelEvent(up)
        self.assertEqual(self.window.current_entry["word"], first)
        before = self.window.config["scale"]
        zoom = QWheelEvent(QPointF(40, 30), QPointF(540, 530), QPoint(),
                           QPoint(0, -120), Qt.MouseButton.NoButton,
                           Qt.KeyboardModifier.ControlModifier,
                           Qt.ScrollPhase.NoScrollPhase, False)
        self.window.wheelEvent(zoom)
        self.assertEqual(self.window.config["scale"], before)
        self.assertNotEqual(self.window.current_entry["word"], first)

    def test_drag_moves_and_edge_resize_changes_size(self):
        self._show()
        self.window.set_expanded(False)
        self.window.move(100, 300)
        start = QPoint(self.window.x(), self.window.y())
        press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(80, 24),
                            QPointF(180, 324), Qt.MouseButton.LeftButton,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.window.mousePressEvent(press)
        move = QMouseEvent(QEvent.Type.MouseMove, QPointF(120, 64),
                           QPointF(220, 364), Qt.MouseButton.NoButton,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.window.mouseMoveEvent(move)
        release = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(120, 64),
                              QPointF(220, 364), Qt.MouseButton.LeftButton,
                              Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        self.window.mouseReleaseEvent(release)
        self.assertNotEqual((self.window.x(), self.window.y()), (start.x(), start.y()))
        self.assertEqual(main.load_config(self.config_path)["position"],
                         [self.window.x(), self.window.y()])

        self.window.move(100, 300)
        width_before = self.window.width()
        right_x = width_before - 1
        press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(right_x, 24),
                            QPointF(100 + right_x, 324), Qt.MouseButton.LeftButton,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.window.mousePressEvent(press)
        move = QMouseEvent(QEvent.Type.MouseMove, QPointF(right_x + 40, 24),
                           QPointF(140 + right_x, 324), Qt.MouseButton.NoButton,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.window.mouseMoveEvent(move)
        release = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(right_x + 40, 24),
                              QPointF(140 + right_x, 324), Qt.MouseButton.LeftButton,
                              Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        self.window.mouseReleaseEvent(release)
        self.assertGreater(self.window.width(), width_before)
        self.assertEqual(main.load_config(self.config_path)["size"][0], self.window.width())

    def test_rating_button_records_and_advances(self):
        self._show()
        self.window._set_hovered(True)
        word = self.window.current_entry["word"]
        rect = self.window.ball.button_rects()["known"]
        QTest.mouseClick(self.window, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
        self.app.processEvents()
        self.assertEqual(self.window.config["study"]["ratings"][word], "known")
        self.assertEqual(self.window.config["study"]["counts"]["known"], 1)
        self.assertNotEqual(self.window.current_entry["word"], word)

    def test_minimum_width_keeps_rating_labels_readable(self):
        self._show()
        minimum_width, _ = main.capsule_min_size(self.window.config["scale"])
        self.window.set_capsule_size(minimum_width, 280, persist=False)
        self.window._set_hovered(True)
        self.assertGreaterEqual(self.window.width(), minimum_width)
        rects = self.window.ball.button_rects()
        scale = self.window.ball.scale_factor
        for key, label in (("known", "认识"), ("fuzzy", "模糊"), ("unknown", "不认识")):
            rect = rects[key]
            self.assertGreater(rect.width(), 0)
            font = QFont("Microsoft YaHei UI", max(8, round(10.5 * scale)))
            font.setPointSize(max(8, round(10.5 * scale)))
            # The painter shrinks the font until the label fits; the smallest
            # allowed size must still fit inside the button at the minimum width.
            font.setPointSize(7)
            self.assertLessEqual(QFontMetrics(font).horizontalAdvance(label),
                                 rect.width() - max(8, 10 * scale))

    def test_unknown_moves_word_just_behind_current(self):
        entries = self.window.entries[:3]
        self.window.deck = list(entries)
        self.window.deck_index = 0
        self.window.show_current()
        first = entries[0]["word"]
        self.window.rate("unknown")
        self.assertEqual([entry["word"] for entry in self.window.deck],
                         [entries[1]["word"], entries[0]["word"], entries[2]["word"]])
        self.assertEqual(self.window.current_entry["word"], entries[1]["word"])
        self.assertEqual(self.window.config["study"]["ratings"][first], "unknown")

    def test_sequence_plan_uses_start_and_daily_count(self):
        entries = self.window.entries
        self.window.apply_study_plan("sequence", 4, 5, [], "CET4")
        self.assertEqual([entry["word"] for entry in self.window.deck],
                         [entry["word"] for entry in entries[4:9]])
        stored = main.load_config(self.config_path)["study"]
        self.assertEqual(stored["mode"], "sequence")
        self.assertEqual(stored["start_index"], 4)
        self.assertEqual(stored["daily_count"], 5)

    def test_custom_plan_limits_to_daily_count(self):
        words = [entry["word"] for entry in self.window.entries[:2]]
        self.window.apply_study_plan("custom", 0, 1, words, "CET4")
        self.assertEqual([entry["word"] for entry in self.window.deck], words[:1])
        self.window.apply_study_plan("custom", 0, 9, words, "CET4")
        self.assertEqual([entry["word"] for entry in self.window.deck], words)
        self.assertEqual(self.window.config["study"]["custom_words"], words)

    def test_wordbook_dialog_returns_ordered_choice(self):
        dialog = main.WordbookDialog(self.directory, "CET4", self.window.config["study"],
                                      self.window.palette, self.window)
        self.assertEqual(dialog.tabs.count(), 2)
        self.assertEqual(dialog.ordered_list.count(), len(self.window.entries))
        dialog.ordered_list.setCurrentRow(3)
        dialog.daily_spin.setValue(7)
        dialog._accept()
        self.assertEqual(dialog.result_mode, "sequence")
        self.assertEqual(dialog.result_vocab, "CET4")
        self.assertEqual(dialog.result_start, 3)
        self.assertEqual(dialog.result_count, 7)

    def test_wordbook_dialog_returns_checked_words(self):
        dialog = main.WordbookDialog(self.directory, "CET4", self.window.config["study"],
                                      self.window.palette, self.window)
        dialog.tabs.setCurrentIndex(1)
        first = dialog.custom_list.item(0)
        first.setCheckState(Qt.CheckState.Checked)
        dialog.daily_spin.setValue(1)
        dialog._accept()
        self.assertEqual(dialog.result_mode, "custom")
        self.assertEqual(dialog.result_custom, [first.data(Qt.ItemDataRole.UserRole)])

    def test_theme_switch_updates_palette_and_config(self):
        self.window.change_theme("blue")
        self.assertEqual(self.window.config["theme"], "blue")
        self.assertEqual(self.window.ball.palette["accent"], main.THEMES["blue"]["accent"])
        self.assertTrue(self.window.theme_actions["blue"].isChecked())
        self.assertEqual(main.load_config(self.config_path)["theme"], "blue")

    def test_click_through_collapses_and_is_saved(self):
        self.window.set_expanded(True)
        self.window.set_click_through(True)
        self.assertFalse(self.window.expanded)
        self.assertTrue(self.window.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.assertTrue(self.window.recover_action.isEnabled())
        self.assertFalse(main.load_config(self.config_path)["click_through"])
        self.window.set_click_through(False)
        self.assertFalse(self.window.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.assertFalse(self.window.recover_action.isEnabled())

    def test_tray_double_click_recovers_from_click_through(self):
        self.window.set_click_through(True)
        self.window._tray_activated(main.QSystemTrayIcon.ActivationReason.DoubleClick)
        self.assertFalse(self.window.config["click_through"])
        self.assertFalse(self.window.recover_action.isEnabled())

    def test_escape_unwinds_click_through_then_pin(self):
        self._show()
        self.assertEqual(self.window.focusPolicy(), Qt.FocusPolicy.StrongFocus)
        self.window.set_click_through(True)
        QTest.keyClick(self.window, Qt.Key.Key_Escape)
        self.assertFalse(self.window.config["click_through"])
        self.window.set_pinned(True)
        self.assertTrue(self.window.pinned)
        QTest.keyClick(self.window, Qt.Key.Key_Escape)
        self.assertFalse(self.window.pinned)
        self.assertFalse(self.window.expanded)

    def test_reset_capsule_restores_defaults(self):
        self.window.set_capsule_size(420, 280, persist=True)
        self.window.set_dock("left", persist=True)
        self.window.reset_capsule()
        stored = main.load_config(self.config_path)
        self.assertIsNone(stored["size"])
        self.assertIsNone(stored["position"])
        self.assertIsNone(stored["dock"])
        self.assertEqual(self.window.dock_side, None)
        self.assertEqual(self.window.width(), main.default_capsule_size(self.window.config["scale"])[0])

    def test_restore_known_today_returns_words_to_deck(self):
        self.window._set_hovered(True)
        word = self.window.current_entry["word"]
        self.window.rate("known")
        self.assertIn(word, self.window.config["study"]["known_today"])
        self.assertNotIn(word, [entry["word"] for entry in self.window.deck])
        self.window.restore_known_today()
        self.assertEqual(self.window.config["study"]["known_today"], [])
        self.assertIn(word, [entry["word"] for entry in self.window.deck])

    def test_detail_fetch_is_disabled_for_tests_and_requests_skip_cache(self):
        self.assertIsNone(self.window.detail_fetcher)
        self.window._request_detail("anything")
        self.assertNotIn("anything", self.window.detail_cache)

    def test_detail_is_applied_and_cached_for_current_word(self):
        self.window._set_hovered(True)
        word = self.window.current_entry["word"]
        detail = {"senses": ["adv. 突然地"], "forms": [], "examples": []}
        self.window._apply_detail(word, detail)
        self.assertEqual(self.window.ball.detail, detail)
        self.assertEqual(self.window.detail_cache[word.lower()], detail)
        self.assertTrue(self.window.detail_cache_path.exists())

    def test_importance_file_covers_common_words(self):
        ranks = main.load_importance()
        self.assertGreater(len(ranks), 9000)
        self.assertEqual(main.stars_for("apple", ranks), 3)
        self.assertEqual(main.stars_for("abruptly", ranks), 1)

    def test_size_and_side_dock_are_persisted(self):
        self.window.set_capsule_size(400, 260, persist=True)
        self.assertEqual(main.load_config(self.config_path)["size"], [400, 260])
        self.window.move(0, 120)
        self.window._snap_to_nearest()
        self.assertEqual(self.window.dock_side, "left")
        self.assertEqual(self.window.x(), self.window._available_geometry().left())
        self.assertEqual(main.load_config(self.config_path)["dock"], "left")

    def test_side_dock_tucks_to_strip_and_reveals_on_edge_hover(self):
        self._show()
        area = self.window._available_geometry()
        self.window.set_dock("left", persist=True)
        self.window.set_expanded(True)
        outside = QPoint(area.right() - 120, area.bottom() - 80)
        self.window._edge_hide_now(outside)
        self.assertTrue(self.window.dock_hidden)
        self.assertFalse(self.window.expanded)
        self.assertEqual(self.window.x(),
                         area.left() - self.window.width() + main.EDGE_STRIP)
        self.assertTrue(self.window._edge_trigger_rect().contains(
            QPoint(area.left() + 2, self.window.y() + 5)))
        self.assertEqual(main.load_config(self.config_path)["position"][0], area.left())

        self.window._edge_tick(QPoint(area.left() + 2, self.window.y() + 5))
        self.assertFalse(self.window.dock_hidden)
        self.assertEqual(self.window.x(), area.left())
        self.assertTrue(self.window.expanded)

    def test_right_side_dock_leaves_a_strip_on_the_right(self):
        self._show()
        area = self.window._available_geometry()
        self.window.set_dock("right", persist=True)
        self.window._edge_hide_now(QPoint(area.left() + 120, area.bottom() - 80))
        self.assertTrue(self.window.dock_hidden)
        self.assertEqual(self.window.x(), area.right() - main.EDGE_STRIP + 1)
        trigger = self.window._edge_trigger_rect()
        self.assertEqual(trigger.right(), area.right())
        self.window._edge_tick(QPoint(area.right() - 2, trigger.center().y()))
        self.assertFalse(self.window.dock_hidden)
        self.assertEqual(self.window.x(), area.right() - self.window.width() + 1)

    def test_pinned_capsule_is_not_tucked_into_the_edge(self):
        self._show()
        area = self.window._available_geometry()
        self.window.set_dock("left", persist=True)
        self.window.set_pinned(True)
        self.window._edge_hide_now(QPoint(area.right() - 120, area.bottom() - 80))
        self.assertFalse(self.window.dock_hidden)
        self.assertTrue(self.window.expanded)

    def test_long_translation_gets_enough_height(self):
        self.window._set_hovered(True)
        self.window.ball.set_content({"word": "perseverance",
                                      "translation": "n. 坚持不懈，毅力。" * 12})
        self.window._ensure_content_fits()
        self.assertGreaterEqual(self.window.height(),
                                self.window.ball.content_min_height_for_width(self.window.width()))
        label = self.window.ball.translation
        self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))

    def test_boss_key_hides_and_restores(self):
        self._show()
        self.window.toggle_boss()
        self.assertTrue(self.window.boss_hidden)
        self.assertFalse(self.window.isVisible())
        self.window.toggle_boss()
        self.assertTrue(self.window.isVisible())

    def test_known_word_is_hidden_for_today_and_persisted(self):
        self._show()
        self.window._set_hovered(True)
        word = self.window.current_entry["word"]
        self.window.rate("known")
        self.assertIn(word, self.window.config["study"]["known_today"])
        self.assertFalse(any(entry["word"] == word for entry in self.window.deck))
        stored = main.load_config(self.config_path)["study"]
        self.assertIn(word, stored["known_today"])
        self.window._rebuild_deck()
        self.assertFalse(any(entry["word"] == word for entry in self.window.deck))

    def test_new_day_clears_known_today_but_keeps_history(self):
        self._show()
        self.window._set_hovered(True)
        word = self.window.current_entry["word"]
        self.window.rate("known")
        study = self.window.config["study"]
        history = study["history"][study["date"]]
        self.assertEqual(history["known"], 1)
        study["date"] = "2000-01-01"
        self.window._ensure_today()
        self.assertEqual(study["date"], date.today().isoformat())
        self.assertEqual(main.load_config(self.config_path)["study"]["date"],
                         date.today().isoformat())
        self.assertEqual(study["known_today"], [])
        self.assertEqual(study["counts"], {"known": 0, "fuzzy": 0, "unknown": 0})
        self.assertEqual(study["history"][study["date"]]["known"], 1)
        self.assertTrue(any(entry["word"] == word for entry in self.window.deck))

    def test_all_known_words_show_completion_state(self):
        self._show()
        self.window._set_hovered(True)
        self.window.deck = [dict(self.window.current_entry)]
        self.window.deck_index = 0
        self.window.show_current()
        self.window.rate("known")
        self.assertEqual(self.window.deck, [])
        self.assertIsNone(self.window.current_entry)
        self.assertEqual(self.window.ball.word, "今日完成")
        self.assertIn("明天", self.window.ball.translation.text())

    def test_completion_state_hides_rating_actions(self):
        self._show()
        self.window._set_hovered(True)
        self.window.deck = []
        self.window.deck_index = 0
        self.window.show_current()
        self.window._set_hovered(True)
        self.app.processEvents()
        self.assertFalse(self.window.ball.show_actions)
        self.assertEqual(self.window.ball.button_rects(), {})

    def test_custom_plan_with_all_words_known_stays_empty(self):
        self._show()
        self.window._set_hovered(True)
        word = self.window.current_entry["word"]
        self.window.apply_study_plan("custom", 0, 30, [word], None)
        self.assertEqual([entry["word"] for entry in self.window.deck], [word])
        self.window.rate("known")
        self.assertEqual(self.window.deck, [])
        self.assertIsNone(self.window.current_entry)

    def test_hover_rolls_over_to_new_day(self):
        self._show()
        self.window._set_hovered(True)
        self.window.rate("known")
        study = self.window.config["study"]
        study["date"] = "2000-01-01"
        self.window._set_hovered(True)
        self.assertEqual(study["date"], date.today().isoformat())
        self.assertEqual(study["known_today"], [])
        self.assertTrue(self.window.expanded)

    def test_unknown_auto_collects_into_my_wordbook(self):
        self._show()
        self.window._set_hovered(True)
        entry = dict(self.window.current_entry)
        word = entry["word"]
        self.window.rate("unknown")
        self.assertEqual([item["word"] for item in self.window.wordbooks[main.AUTO_WORD_BOOK]], [word])
        saved = main.load_wordbooks(self.window.wordbooks_path)
        self.assertEqual([item["word"] for item in saved[main.AUTO_WORD_BOOK]], [word])
        self.assertFalse(self.window._add_entry_to_book(main.AUTO_WORD_BOOK, entry))
        self.assertEqual(len(self.window.wordbooks[main.AUTO_WORD_BOOK]), 1)

    def test_rating_history_backs_calendar(self):
        self._show()
        self.window._set_hovered(True)
        self.window.rate("known")
        self.window.rate("unknown")
        study = self.window.config["study"]
        day = study["history"][study["date"]]
        self.assertEqual(day, {"known": 1, "fuzzy": 0, "unknown": 1})
        dialog = main.StudyCalendarDialog(study["history"], self.window.palette, self.window)
        self.assertIn("认识 1", dialog.day_detail.text())
        self.assertIn("不认识 1", dialog.day_detail.text())
        self.assertIn("2 次", dialog.month_label.text())
        dialog.close()

    def test_add_button_opens_wordbook_picker(self):
        self._show()
        self.window._set_hovered(True)
        add = self.window.ball.button_rects()["add"]
        self.assertGreater(add.width(), 0)
        with patch.object(self.window, "choose_wordbook_for_current") as choose:
            QTest.mouseClick(self.window, Qt.MouseButton.LeftButton,
                             pos=add.center().toPoint())
            self.app.processEvents()
            choose.assert_called_once()

    def test_plus_menu_lists_books_and_adds_current_word(self):
        self._show()
        self.window._set_hovered(True)
        word = self.window.current_entry["word"]
        self.window.wordbooks["生词"] = []
        menu = self.window._build_wordbook_menu()
        labels = [action.text() for action in menu.actions() if not action.isSeparator()]
        self.assertTrue(any(label.startswith(main.AUTO_WORD_BOOK) for label in labels))
        self.assertTrue(any(label.startswith("生词") for label in labels))
        self.assertIn("新建单词本…", labels)
        menu.actions()[0].trigger()
        self.assertEqual([item["word"] for item in self.window.wordbooks[main.AUTO_WORD_BOOK]], [word])
        menu.close()

    def test_wordbook_manager_creates_book_and_starts_it(self):
        self._show()
        self.window._set_hovered(True)
        entry = dict(self.window.current_entry)
        dialog = main.MyWordbooksDialog(copy.deepcopy(self.window.wordbooks),
                                        self.window.palette, self.window)
        with patch("main.prompt_text", return_value=("复习", True)):
            dialog._new_book()
        self.assertIn("复习", dialog.books)
        dialog.books["复习"] = [entry]
        dialog._reload_books("复习")
        self.assertEqual(dialog.words_list.count(), 1)
        dialog._start()
        self.assertEqual(dialog.result_book, "复习")
        dialog.close()

        self.window.wordbooks["复习"] = [entry]
        self.window.apply_wordbook("复习")
        self.assertEqual(self.window.config["study"]["mode"], "book")
        self.assertEqual(self.window.config["study"]["book"], "复习")
        self.assertEqual(self.window.current_entry["word"], entry["word"])

    def test_wordbook_add_can_choose_another_book(self):
        self._show()
        self.window._set_hovered(True)
        word = self.window.current_entry["word"]
        self.window.wordbooks["生词"] = []
        self.assertTrue(self.window.add_current_to_book("生词"))
        self.assertEqual(self.window.wordbooks["生词"][0]["word"], word)
        self.assertTrue((self.directory / "wordbooks.json").exists())

    def test_dialog_side_position_never_covers_anchor(self):
        area = QRect(0, 0, 1536, 816)
        anchor = QRect(186, 0, 352, 269)
        position = main.dialog_side_position(anchor, QSize(705, 753), area)
        dialog_rect = QRect(position, QSize(705, 753))
        self.assertFalse(dialog_rect.intersects(anchor))
        self.assertTrue(area.contains(dialog_rect))
        self.assertGreaterEqual(position.x(), anchor.right() + 1)

        edge_anchor = QRect(1200, 100, 300, 200)
        position = main.dialog_side_position(edge_anchor, QSize(300, 400), area)
        dialog_rect = QRect(position, QSize(300, 400))
        self.assertFalse(dialog_rect.intersects(edge_anchor))
        self.assertLessEqual(dialog_rect.right(), edge_anchor.left())

    def test_present_dialog_is_always_on_top_and_beside_capsule(self):
        dialog = QInputDialog(self.window)
        dialog.setWindowTitle("置顶测试")
        captured = {}

        def inspect_and_close():
            captured["geometry"] = QRect(dialog.geometry())
            captured["flags"] = dialog.windowFlags()
            dialog.reject()

        QTimer.singleShot(30, inspect_and_close)
        main.present_dialog(dialog)
        self.assertTrue(bool(captured["flags"] & Qt.WindowType.WindowStaysOnTopHint))
        self.assertFalse(captured["geometry"].intersects(self.window.frameGeometry()))

    def test_present_dialog_keeps_custom_dialog_size(self):
        dialog = QDialog(self.window)
        dialog.resize(560, 570)
        captured = {}

        def close():
            captured["size"] = dialog.size()
            dialog.reject()

        QTimer.singleShot(30, close)
        main.present_dialog(dialog)
        self.assertGreaterEqual(captured["size"].width(), 560)
        self.assertGreaterEqual(captured["size"].height(), 570)

    def test_windows_hotkey_registration_is_recorded(self):
        if main.sys.platform != "win32":
            self.skipTest("Windows-only")
        hotkeys = main.WindowsHotkeys(self.window)
        with patch.object(hotkeys.user32, "RegisterHotKey", side_effect=[1, 0]) as register:
            hotkeys.start()
            self.assertEqual(hotkeys.registered, {"boss": True, "through": False})
            self.assertEqual(register.call_count, 2)
        hotkeys.stop()


class RuntimePathTests(unittest.TestCase):
    def test_data_dir_honors_environment_override(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(main.os.environ, {"FLOATINGWORDBALL_DATA_DIR": directory}):
                self.assertEqual(main.default_data_dir().resolve(), Path(directory).resolve())

    def test_prepare_data_dir_migrates_legacy_files_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy, destination = root / "legacy", root / "data"
            legacy.mkdir()
            destination.mkdir()
            (legacy / "config.json").write_text('{"theme": "blue"}', encoding="utf-8")
            (legacy / "wordbooks.json").write_text('{"books": {}}', encoding="utf-8")
            (legacy / "details_cache.json").write_text('{"entries": {}}', encoding="utf-8")
            (destination / "config.json").write_text('{"theme": "amber"}', encoding="utf-8")
            main.prepare_data_dir(destination, legacy)
            self.assertEqual(json.loads((destination / "config.json").read_text("utf-8"))["theme"],
                             "amber")
            self.assertTrue((destination / "wordbooks.json").is_file())
            self.assertTrue((destination / "details_cache.json").is_file())

    def test_prepare_vocab_dir_copies_bundled_vocab_and_fills_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundled, destination = root / "bundled", root / "data"
            bundled.mkdir()
            (bundled / "CET4.json").write_text(
                json.dumps([{"word": "harbor", "translation": "n. 港口"}]), encoding="utf-8")
            vocab_dir = main.prepare_vocab_dir(destination, bundled)
            self.assertEqual(load_vocab(vocab_dir / "CET4.json")[0]["word"], "harbor")
            for name in main.VOCABULARIES:
                self.assertTrue((vocab_dir / f"{name}.json").is_file())

    def test_corrupt_config_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("{not valid json", encoding="utf-8")
            config = main.load_config(path)
            self.assertEqual(config["theme"], "green")
            self.assertEqual(config["vocabulary"], "CET4")
            self.assertEqual(config["scale"], 1.0)


if __name__ == "__main__":
    unittest.main()
