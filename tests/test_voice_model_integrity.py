import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from assistant import voice


class SileroModelIntegrityTests(unittest.TestCase):
    def test_rejects_download_when_sha256_does_not_match(self):
        payload = b"x" * (voice.SILERO_MODEL_MIN_BYTES + 1)
        with tempfile.TemporaryDirectory() as temp_dir:
            model_path = Path(temp_dir) / "v4_ru.pt"
            with patch.object(voice, "_silero_model_path", return_value=model_path), \
                 patch.object(voice.urllib.request, "urlopen", return_value=io.BytesIO(payload)), \
                 patch.object(voice, "SILERO_MODEL_SHA256", "0" * 64):
                with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                    voice._ensure_silero_model()

            self.assertFalse(model_path.exists())
            self.assertFalse(model_path.with_name(model_path.name + ".part").exists())


if __name__ == "__main__":
    unittest.main()
