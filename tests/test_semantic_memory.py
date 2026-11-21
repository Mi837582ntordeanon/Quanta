import tempfile
import unittest
from pathlib import Path

from assistant.semantic_memory import SemanticMemoryIndex, cosine


class FakeEmbedder:
    def embed(self, texts):
        for text in texts:
            low = text.casefold()
            vector = [0.0] * 384
            if any(token in low for token in ("звуч", "голос", "тембр", "speech")):
                vector[0] = 1.0
            else:
                vector[1] = 1.0
            yield vector


class SemanticMemoryTests(unittest.TestCase):
    def test_semantic_index_ranks_paraphrase_and_persists_vectors(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            index = SemanticMemoryIndex(root_path / "models", root_path / "vectors.json")
            index._state = "ready"
            index._embedder = FakeEmbedder()
            memories = [
                {"id": "voice1234", "text": "Молодой женский тембр и озорной характер", "updated": "2026-01-01"},
                {"id": "ui123456", "text": "Тёмная тема интерфейса и компактные панели", "updated": "2026-01-02"},
            ]

            found = index.search("Как должна звучать Кванта?", memories)

            self.assertEqual([item["id"] for item in found], ["voice1234"])
            self.assertTrue((root_path / "vectors.json").is_file())
            restarted = SemanticMemoryIndex(root_path / "models", root_path / "vectors.json")
            self.assertEqual(len(restarted._vectors), 2)

    def test_forgetting_memory_removes_its_embedding(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            index = SemanticMemoryIndex(root_path / "models", root_path / "vectors.json")
            index._vectors["private123"] = {"digest": "x", "vector": [0.0] * 384}
            index._save_vectors()
            index.remove_memory("private123")
            self.assertEqual(index._vectors, {})
            self.assertEqual((root_path / "vectors.json").read_text(encoding="utf-8"), "{}")

    def test_cosine_handles_zero_vectors_and_ranks_direction(self):
        self.assertEqual(cosine([0.0, 0.0], [1.0, 0.0]), 0.0)
        self.assertGreater(cosine([1.0, 0.0], [0.9, 0.1]), cosine([1.0, 0.0], [0.0, 1.0]))


if __name__ == "__main__":
    unittest.main()
