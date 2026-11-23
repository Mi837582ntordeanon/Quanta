"""Contract tests for decorated built-in tool metadata."""

import ast
import tempfile
import unittest
from pathlib import Path
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

    def test_network_tools_and_transport_imports_cannot_be_marked_offline(self):
        with tempfile.TemporaryDirectory() as root:
            toolbox = Toolbox(SimpleNamespace(settings={"work_dir": root}))

        previously_networked = {
            "web_search", "fetch_url", "weather", "generate_image",
            "yandex_music", "minecraft_status",
        }
        for name in sorted(previously_networked):
            with self.subTest(tool=name):
                self.assertIn(name, toolbox.tools)
                self.assertIs(toolbox.tools[name]["network"], True)

        def imports_network_dependencies(tree):
            imported_names = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_names.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    module = ("." * node.level) + (node.module or "")
                    imported_names.append(module)
                    for alias in node.names:
                        imported_names.append(alias.name)
                        if module:
                            imported_names.append(f"{module}.{alias.name}")
            for name in imported_names:
                normalized = name.lstrip(".")
                if (
                    normalized == "network_security"
                    or normalized.endswith(".network_security")
                    or normalized == "urllib"
                    or normalized.startswith("urllib.")
                    or normalized == "http.client"
                ):
                    return True
            return False

        def is_tool_decorator(expression):
            if isinstance(expression, ast.Call):
                expression = expression.func
            return (
                isinstance(expression, ast.Name) and expression.id == "tool"
            ) or (
                isinstance(expression, ast.Attribute) and expression.attr == "tool"
            )

        package = Path(__file__).resolve().parents[1] / "assistant" / "tools"
        violations = []
        for module_path in sorted(package.glob("*.py")):
            tree = ast.parse(module_path.read_text(encoding="utf-8"))
            if not imports_network_dependencies(tree):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not any(is_tool_decorator(item) for item in node.decorator_list):
                    continue
                network = next((
                    keyword.value
                    for decorator in node.decorator_list
                    if isinstance(decorator, ast.Call) and is_tool_decorator(decorator)
                    for keyword in decorator.keywords if keyword.arg == "network"
                ), None)
                if isinstance(network, ast.Constant) and network.value is False:
                    violations.append(f"{module_path.name}:{node.name}")
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
