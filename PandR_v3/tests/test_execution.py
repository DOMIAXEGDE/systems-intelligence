"""Execute real child processes to verify bounded lifecycle management."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import time
import unittest

from pandr.execution import ExecutionService


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pandr-execution-test-")
        self.directory = Path(self.temp.name)
        self.session = self.directory / "session.json"
        self.session.write_text("{}", encoding="utf-8")
        self.service = ExecutionService()

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def run_source(self, source, **options):
        result = self.service.run_script(source, self.session, **options)
        self.assertEqual(self.service.active_count, 0)
        return result

    def test_success_unicode_stream_and_environment(self):
        chunks = []
        result = self.run_source(
            "import os,sys\nprint('P&R: Ω ↑ ↓')\nprint(os.environ['PANDR_SESSION'])\nprint('stderr',file=sys.stderr)",
            on_output=chunks.append,
        )
        self.assertEqual(result["status"], "success", result)
        self.assertIn("Ω ↑ ↓", result["output"])
        self.assertIn(str(self.session), result["output"])
        self.assertIn("stderr", result["output"])
        self.assertEqual("".join(chunks), result["output"])
        self.assertEqual(result["working_directory"], str(self.directory))
        self.assertTrue(result["trusted_python"])
        self.assertFalse(result["construction_replayable"])

    def test_exception_is_captured(self):
        result = self.run_source("raise RuntimeError('operator failure')")
        self.assertEqual(result["status"], "error")
        self.assertNotEqual(result["returncode"], 0)
        self.assertIn("operator failure", result["output"])

    def test_output_budget_terminates_unbounded_writer(self):
        chunks = []
        result = self.run_source("while True: print('a'*8192)", max_output=1000, on_output=chunks.append)
        self.assertEqual(result["status"], "output_limit", result)
        self.assertTrue(result["output_truncated"])
        self.assertEqual(len(result["output"].encode("utf-8")), 1000)
        self.assertEqual("".join(chunks), result["output"])
        self.assertLess(result["duration_ms"], 5000)

    def test_timeout_and_consumer_error_cleanup(self):
        def bad_consumer(chunk):
            raise RuntimeError("GUI log consumer failed")
        result = self.run_source("import time\nprint('started')\ntime.sleep(30)", timeout=0.35, on_output=bad_consumer)
        self.assertEqual(result["status"], "timeout", result)
        self.assertLess(result["duration_ms"], 5000)

    def test_cancel_and_reuse_service(self):
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.service.run_script, "import time; time.sleep(30)", self.session)
            deadline = time.monotonic() + 5
            while self.service.active_count == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(self.service.active_count, 1)
            self.service.cancel_all()
            result = future.result(timeout=5)
        self.assertEqual(result["status"], "cancelled", result)
        self.assertEqual(self.run_source("print('next run')")["status"], "success")

    def test_descendants_are_reaped_after_parent_finishes(self):
        sentinel = self.directory / "descendant-finished.txt"
        child = f"import time; from pathlib import Path; time.sleep(0.7); Path({str(sentinel)!r}).write_text('escaped')"
        source = f"import subprocess,sys\nsubprocess.Popen([sys.executable,'-c',{child!r}])\nprint('parent complete')"
        result = self.run_source(source)
        self.assertEqual(result["status"], "success", result)
        time.sleep(0.85)
        self.assertFalse(sentinel.exists(), "An owned script descendant survived normal completion")

    def test_reject_invalid_bounds_before_spawning(self):
        for options in ({"timeout": 0}, {"timeout": float("nan")}, {"timeout": True},
                        {"max_output": True}, {"max_output": 0}, {"max_output": 4_194_305}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.run_source("print(1)", **options)
        with self.assertRaises(ValueError):
            self.run_source(" ")


if __name__ == "__main__":
    unittest.main()
