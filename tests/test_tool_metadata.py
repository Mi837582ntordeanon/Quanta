"""Contract tests for decorated built-in tool metadata."""

import tempfile
import unittest
from types import SimpleNamespace

from assistant.tools import Toolbox, tool


class ToolMetadataTests(unittest.TestCase):
    def test_capabilities_are_required_and_non_empty(self):
        arguments = {
            "name": "sample",
            "label": "Sample",
            "description": "Sample tool",
            "parameters": {"type": "object", "properties": {}},
        }
        with self.assertRaises(TypeError):
            tool(**arguments)
        for capabilities in ([], ["", "  "]):
            with self.subTest(capabilities=capabilities), self.assertRaises(ValueError):
                tool(**arguments, capabilities=capabilities)

    def test_network_defaults_to_enabled_and_offline_opt_out_is_explicit(self):
        arguments = {
            "name": "sample",
            "label": "Sample",
            "description": "Sample tool",
            "parameters": {"type": "object", "properties": {}},
            "capabilities": ["local_read"],
        }
        network_tool = tool(**arguments)(lambda: None)
        offline_tool = tool(**arguments, network=False)(lambda: None)
        self.assertTrue(network_tool.__tool_spec__.network)
        self.assertFalse(offline_tool.__tool_spec__.network)

    def test_every_built_in_declares_capabilities_and_an_offline_decision(self):
        with tempfile.TemporaryDirectory() as root:
            toolbox = Toolbox(SimpleNamespace(settings={"work_dir": root}))
        for name, metadata in toolbox.tools.items():
            with self.subTest(name=name):
                self.assertTrue(metadata["capabilities"])
                self.assertIsInstance(metadata["network"], bool)
        self.assertFalse(toolbox.tools["read_file"]["network"])
        self.assertTrue(toolbox.tools["fetch_url"]["network"])


if __name__ == "__main__":
    unittest.main()
