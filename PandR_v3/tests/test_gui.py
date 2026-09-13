"""Real Tk integration checks; only the host window may be unavailable headless."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pandr.expressions import Function, number_is
from pandr.gui import PandRApp
from pandr.runtime import Runtime


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pandr-gui-test-")
        self.directory = Path(self.temp.name)
        self.runtime = Runtime.create(self.directory / "source.json")
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.temp.cleanup()
            self.skipTest(f"Tk display unavailable: {error}")
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.app = PandRApp(self.root, self.runtime)
        report = self.app._show_error
        def capture(action, error):
            self.errors.append((action, error))
            report(action, error)
        self.app._show_error = capture
        self.root.deiconify()
        self.settle()

    def tearDown(self):
        if hasattr(self, "app"):
            self.app.close()
            self.app._executor.shutdown(wait=True)
        self.temp.cleanup()

    def settle(self, seconds=15):
        deadline = time.monotonic() + seconds
        quiet_since = None
        while time.monotonic() < deadline:
            self.root.update()
            if self.app._busy == 0 and self.app._plot_after is None:
                quiet_since = quiet_since or time.monotonic()
                if time.monotonic() - quiet_since > 0.12:
                    break
            else:
                quiet_since = None
            time.sleep(0.01)
        else:
            self.fail(f"GUI did not settle: {self.app.status.get()}")
        self.assertFalse(self.errors, self.errors)

    def test_general_functions_and_combined_plot(self):
        self.app.function_var.set('union(sin(x), abs(x), implicit(x**2+y**2-25))')
        self.app._preview_function()
        self.settle()
        self.app._set_function()
        self.settle()
        self.assertEqual({i for i, _ in self.app._plot_segments}, {0, 1, 2})
        self.assertIn('combined curves', self.app.curve_label.cget('text'))
        colors = {self.app.canvas.itemcget(item, 'fill') for item in self.app.canvas.find_all()
                  if self.app.canvas.type(item) == 'line'}
        self.assertTrue({'#2463dd', '#e11d48', '#059669'} <= colors)
        self.app.plot_resolution.set('320')
        self.app._schedule_plot()
        self.settle()
        self.assertTrue(self.app._plot_segments)

    def test_all_panels_four_rays_transform_sequence_and_pixel_editor(self):
        self.assertEqual([self.app.tabs.tab(tab, "text") for tab in self.app.tabs.tabs()],
                         ["Function", "Sequence", "Pixels", "Python", "Signals", "Media", "Journal"])
        for tab in self.app.tabs.tabs():
            self.app.tabs.select(tab)
            self.root.update()
            self.assertGreater(self.root.nametowidget(tab).winfo_height(), 100)
        self.app.select_point("p0")
        self.app.select_layer("geometry")
        rows = [self.app.trace.item(row, "values") for row in self.app.trace.get_children()]
        self.assertEqual({(row[0], row[1], row[2], row[3]) for row in rows}, {
            ("v", "horizontal", "right", "17"), ("h", "horizontal", "left", "2"),
            ("v", "vertical", "down", "17"), ("h", "vertical", "up", "2")})
        colors = {self.app.canvas.itemcget(item, "fill") for item in self.app.canvas.find_all()
                  if self.app.canvas.type(item) == "line"}
        self.assertTrue({"#059669", "#d97706", "#8b5cf6", "#dc4673"} <= colors)
        self.app._replace(self.app.sequence_text, "v17 + v-down17 + h-up2")
        preview = self.app._resolve_sequence(False)
        self.settle()
        self.assertEqual({event["plane"] for event in preview.result()}, {"horizontal", "vertical"})
        self.app._resolve_sequence(True)
        self.settle()
        self.assertEqual(len(self.app.runtime.snapshot()["domain"]["last_events"]), 3)
        before_points = deepcopy(self.app._points)
        self.app._transform("translate", dx="0", dy="5")
        self.settle()
        self.app._transform("postcompose", function="2*x")
        self.settle()
        function = Function.from_dict(self.app.runtime.snapshot()["domain"]["functions"]["main"])
        self.assertTrue(number_is(function.evaluate("3"), "16"))
        self.assertEqual(self.app._points, before_points)
        previous = self.app._alphabet["version"]
        self.app._toggle_pixel(SimpleNamespace(x=11, y=11))
        self.app._save_glyph()
        self.settle()
        self.assertEqual(self.app._alphabet["version"], previous + 1)
        self.app._toggle_layer()
        self.settle()
        self.assertFalse(self.app.runtime.snapshot()["domain"]["layers"][0]["enabled"])

    def test_python_media_signal_and_persistence_workflows(self):
        self.app.set_code("import os\nfrom pandr import Runtime\np=Runtime.open(os.environ['PANDR_SESSION'])\np.commit(p.plan().transform.translate(dy='3'))\nprint('operator-run-ok')\n")
        self.app.execute_script()
        self.settle()
        self.assertIn("operator-run-ok", self.app.console.get("1.0", "end-1c"))
        function = Function.from_dict(self.app.runtime.snapshot()["domain"]["functions"]["main"])
        self.assertTrue(number_is(function.evaluate("0"), "3"))
        self.assertIn("operator-main", self.app.runtime.snapshot()["programs"])
        for kind in ("text", "image", "audio", "video", "code"):
            self.app.media_kind.set(kind)
            self.app._render()
            self.settle()
            artifact = next(a for a in self.app._artifacts if a.get("kind") == kind)
            self.assertTrue((self.directory / artifact["path"]).is_file())
            index = self.app._artifacts.index(artifact)
            self.app.artifact_list.selection_set(str(index))
            self.app._preview_artifact()
            self.assertTrue(self.app._artifact_path.is_file())
        destination = Runtime.create(self.directory / "receiver.json")
        self.app.destination_var.set(str(destination.path))
        self.app._send_signal()
        self.settle()
        self.app._drain()
        self.settle()
        self.assertEqual(len(Runtime.open(destination.path).snapshot()["inbox"]), 1)
        self.app.select_point("p1")
        self.app.select_layer("text")
        self.app._replace(self.app.notes_text, "A persisted operator note.")
        self.app.function_var.set("x+41")
        self.app._replace(self.app.sequence_text, "h-up3 + v-down7")
        self.app.sequence_match.set("first_ordered")
        self.app._save_editors()
        self.settle()
        self.app._switch_runtime(Runtime.open(self.app.runtime.path))
        self.settle()
        self.assertEqual(self.app.notes_text.get("1.0", "end-1c"), "A persisted operator note.")
        self.assertEqual(self.app.function_var.get(), "x+41")
        self.assertEqual(self.app.sequence_text.get("1.0", "end-1c"), "h-up3 + v-down7")
        self.assertEqual(self.app.sequence_match.get(), "first_ordered")
        self.assertEqual(self.app._selected_point, "p1")
        self.assertEqual(self.app._selected_layer, "text")
        replay_path = self.directory / "replayed.json"
        with patch("pandr.gui.filedialog.asksaveasfilename", return_value=str(replay_path)):
            self.app._replay()
            self.settle()
        self.assertEqual(Runtime.open(replay_path).snapshot()["integrity"]["domain_hash"],
                         self.app.runtime.snapshot()["integrity"]["domain_hash"])

    def test_default_vertical_preserves_explicit_horizontal_and_import(self):
        self.app.sequence_plane.set("vertical")
        self.app._replace(self.app.sequence_text, "v17 + v-horizontal17 + h-up2")
        future = self.app._resolve_sequence(False)
        self.settle()
        self.assertEqual([event["plane"] for event in future.result()], ["vertical", "horizontal", "vertical"])
        from pandr.alphabets import create_alphabet
        manifest = create_alphabet("imported", count=4, width=5, height=5)
        path = self.directory / "alphabet.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with patch("pandr.gui.filedialog.askopenfilename", return_value=str(path)):
            self.app._import_alphabet()
            self.settle()
        self.assertEqual(self.app._alphabet["id"], "imported")


if __name__ == "__main__":
    unittest.main()
