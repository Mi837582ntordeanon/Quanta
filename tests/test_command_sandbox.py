import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from assistant import command_sandbox


class CommandSandboxTests(unittest.TestCase):
    def test_config_disables_shared_host_channels_and_maps_only_selected_folders(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "workspace"
            ipc = Path(root) / "ipc"
            workspace.mkdir()
            ipc.mkdir()
            xml_text = command_sandbox.build_configuration(workspace, ipc, "powershell.exe -EncodedCommand AQID")
            config = ET.fromstring(xml_text)
            self.assertEqual(config.findtext("Networking"), "Disable")
            self.assertEqual(config.findtext("ClipboardRedirection"), "Disable")
            self.assertEqual(config.findtext("VGpu"), "Disable")
            mapped = config.findall("./MappedFolders/MappedFolder")
            self.assertEqual([item.findtext("HostFolder") for item in mapped],
                             [str(workspace.resolve()), str(ipc.resolve())])
            self.assertEqual([item.findtext("ReadOnly") for item in mapped], ["false", "false"])
            self.assertEqual(config.findtext("./LogonCommand/Command"), "powershell.exe -EncodedCommand AQID")

    def test_runner_encodes_the_command_and_does_not_bypass_execution_policy(self):
        script = command_sandbox._guest_runner(
            "Get-Content C:\\secret.txt", r"C:\QuantaWorkspace\project", "powershell", 15,
        )
        self.assertIn("FromBase64String", script)
        self.assertIn("-EncodedCommand", script)
        self.assertNotIn("ExecutionPolicy Bypass", script)
        self.assertNotIn("Get-Content C:\\secret.txt", script)
        self.assertIn("$timeoutMs = 15000", script)
        self.assertIn("WaitForExit($timeoutMs)", script)

    def test_missing_windows_sandbox_fails_closed_without_starting_a_process(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            with patch("assistant.command_sandbox._sandbox_executable", return_value=None), \
                 patch("assistant.command_sandbox.subprocess.Popen") as popen:
                result = command_sandbox.run("whoami", folder, folder)
            self.assertFalse(result["ok"])
            self.assertIn("не запускалась", result["text"])
            popen.assert_not_called()

    def test_runner_refuses_a_working_directory_outside_the_mapped_folder(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            with patch("assistant.command_sandbox._sandbox_executable", return_value=Path(root) / "WindowsSandbox.exe"), \
                 patch("assistant.command_sandbox.subprocess.Popen") as popen:
                result = command_sandbox.run("whoami", Path(outside), Path(root), data_dir=Path(root))
            self.assertFalse(result["ok"])
            self.assertIn("внутри выбранной папки", result["text"])
            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
