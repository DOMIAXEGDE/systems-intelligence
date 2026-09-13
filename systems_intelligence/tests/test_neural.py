import importlib.util
from pathlib import Path
import tempfile
import unittest

HAS_TORCH = importlib.util.find_spec("torch") is not None


@unittest.skipUnless(HAS_TORCH, "Optional PyTorch dependency is not installed")
class NeuralTests(unittest.TestCase):
    def setUp(self):
        import torch
        from framelm.neural import ByteTransformer
        torch.set_num_threads(1)
        torch.manual_seed(3)
        self.model = ByteTransformer({"width": 16, "heads": 2, "layers": 1, "block_size": 64}).eval()

    def test_no_future_token_leakage(self):
        import torch
        a, b = torch.tensor([[1, 2, 3, 4]]), torch.tensor([[1, 2, 55, 66]])
        with torch.no_grad():
            torch.testing.assert_close(self.model(a)[:, :2], self.model(b)[:, :2])

    def test_backward_and_checkpoint_roundtrip(self):
        import torch
        from framelm.neural import load_model
        x = torch.tensor([[1, 2, 3]])
        loss = self.model(x).square().mean()
        loss.backward()
        self.assertTrue(torch.isfinite(self.model.tokens.weight.grad).all())
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "model.pt"
            torch.save({"format": "framelm-byte-v1", "config": self.model.config, "model": self.model.state_dict()}, path)
            loaded = load_model(path)
            with torch.no_grad():
                torch.testing.assert_close(loaded(x), self.model(x))

    def test_prompt_mask_and_answer_alignment(self):
        from framelm.train import encode_record
        from framelm.neural import EOS
        x, y = encode_record({"prompt": "group", "contexts": ["A group"], "response": "Yes"})
        self.assertEqual(len(x), len(y))
        self.assertEqual([v for v in y if v != -100], [89, 101, 115, EOS])

    def test_generation_budget_rejected(self):
        from framelm.neural import generate
        with self.assertRaises(ValueError):
            generate(self.model, "a" * 64, 1)


if __name__ == "__main__":
    unittest.main()
