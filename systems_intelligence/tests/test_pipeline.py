import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from framelm.core import (Candidate, Context, DEFAULTS, Pipeline, SQLiteRetriever,
                          frame_prompt, load_config, score_candidate)
from framelm.backends import OllamaBackend


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.docs = self.root / "contexts"
        self.docs.mkdir()
        (self.docs / "math.txt").write_text("An abelian group is a group with a commutative operation.", encoding="utf-8")
        (self.docs / "plants.md").write_text("A fern is a vascular plant that reproduces using spores.", encoding="utf-8")
        self.cfg = DEFAULTS | {"index_path": str(self.root / "index.db")}
        self.index = SQLiteRetriever(Path(self.cfg["index_path"]))
        self.index.build(self.docs)

    def test_relevant_answer_and_citation(self):
        r = Pipeline(self.cfg).run("What is an abelian group?")
        self.assertEqual(r["status"], "resolved")
        self.assertIn("commutative", r["response"])
        self.assertEqual(r["contexts"][0]["source"], "math.txt")
        self.assertNotIn("fern", r["response"])

    def test_unknown_question_abstains_without_model_call(self):
        class NoCall:
            def generate(self, *args):
                raise AssertionError("Backend must not be called")
        r = Pipeline(self.cfg, backend=NoCall()).run("Explain superconductivity")
        self.assertEqual(r["status"], "insufficient_context")

    def test_empty_and_oversized_prompts(self):
        for prompt in ["  ", "x" * 8001]:
            with self.assertRaises(ValueError):
                Pipeline(self.cfg).run(prompt)

    def test_fts_syntax_is_not_executed(self):
        r = Pipeline(self.cfg).run('abelian " OR * NEAR(group) -column:foo')
        self.assertIn(r["status"], {"resolved", "insufficient_context"})

    def test_rebuild_removes_deleted_content(self):
        (self.docs / "math.txt").unlink()
        self.index.build(self.docs)
        self.assertEqual(self.index.search(frame_prompt("abelian"), 5), [])

    def test_failed_rebuild_retains_previous_index(self):
        (self.docs / "bad.txt").write_bytes(b"\xff")
        with self.assertRaises(UnicodeDecodeError):
            self.index.build(self.docs)
        self.assertTrue(self.index.search(frame_prompt("abelian"), 5))

    def test_budget_enforced(self):
        r = Pipeline(self.cfg | {"context_char_budget": 20}).run("group")
        self.assertLessEqual(sum(len(c["text"]) for c in r["contexts"]), 20)

    def test_unicode_retrieval(self):
        (self.docs / "unicode.txt").write_text("Le café contient une description française.", encoding="utf-8")
        self.index.build(self.docs)
        self.assertEqual(self.index.search(frame_prompt("café"), 5)[0].source, "unicode.txt")

    def test_fabricated_citation_rejected(self):
        class BadCitation:
            def generate(self, *args):
                return [Candidate("An abelian group is commutative [C99]", "fake")]
        r = Pipeline(self.cfg | {"include_extractive_fallback": False}, backend=BadCitation()).run("abelian group")
        self.assertEqual(r["status"], "unresolved")

    def test_backend_failure_is_visible_and_falls_back(self):
        class Broken:
            def generate(self, *args):
                raise RuntimeError("server unavailable")
        r = Pipeline(self.cfg | {"backend": "ollama"}, backend=Broken()).run("abelian group")
        self.assertEqual(r["selected_backend"], "extractive")
        self.assertIn("server unavailable", r["warnings"][0])

    def test_best_candidate_selected(self):
        class Two:
            def generate(self, *args):
                return [Candidate("Unrelated waffles [C1]", "bad"),
                        Candidate("An abelian group has a commutative operation. [C1]", "good")]
        r = Pipeline(self.cfg, backend=Two()).run("abelian group")
        self.assertEqual(r["selected_backend"], "good")

    def test_ollama_payload_and_response_contract(self):
        from io import BytesIO
        with patch("framelm.backends.urlopen", return_value=BytesIO(b'{"message":{"content":"Answer [C1]"}}')) as mock:
            cfg = self.cfg | {"model": "test-model", "candidate_count": 1}
            output = OllamaBackend().generate(frame_prompt("group"), [Context(1, "math", "A group", 1)], cfg)
            request = mock.call_args.args[0]
            payload = json.loads(request.data)
            self.assertFalse(payload["stream"])
            self.assertEqual(payload["model"], "test-model")
            self.assertEqual(output[0].text, "Answer [C1]")

    def test_invalid_config_and_relative_paths(self):
        configs = self.root / "configs"
        configs.mkdir()
        path = configs / "demo.json"
        path.write_text('{"top_k":0}')
        with self.assertRaises(ValueError):
            load_config(path)
        path.write_text('{}')
        self.assertEqual(load_config(path)["context_dir"], str(self.docs))


if __name__ == "__main__":
    unittest.main()
