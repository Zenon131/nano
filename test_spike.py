"""Regression test for tool discovery and Nano's streamed agent loop."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from nano_agent.core import Nano


class NanoLoopTest(unittest.TestCase):
    def test_discovery_and_streamed_tool_loop(self):
        with tempfile.TemporaryDirectory(prefix="nano-loop-test-") as temporary:
            root = Path(temporary)
            (root / "marker.txt").write_text("example", encoding="utf-8")
            previous = Path.cwd()
            try:
                os.chdir(root)
                with patch.dict(os.environ, {"HOME": temporary, "OPENAI_API_KEY": "test-key"}), \
                     patch("nano_agent.core.ContextImporter.discover_and_import", return_value=[]):
                    agent = Nano(confirm_callback=lambda name, args: False)
                    self.assertIn("list_files", agent.registry.tools)

                    call = SimpleNamespace(
                        index=0,
                        id="call_123",
                        function=SimpleNamespace(name="list_files", arguments=json.dumps({"path": temporary})),
                    )
                    first = SimpleNamespace(choices=[SimpleNamespace(
                        delta=SimpleNamespace(content=None, tool_calls=[call]))])
                    second = SimpleNamespace(choices=[SimpleNamespace(
                        delta=SimpleNamespace(content="I found the files!", tool_calls=None))])

                    with patch.object(agent.client.chat.completions, "create", side_effect=[iter([first]), iter([second])]) as create:
                        result = asyncio.run(agent.run("What files are here?"))

                    self.assertEqual(result, "I found the files!")
                    self.assertEqual(create.call_count, 2)
                    tool_messages = [message for message in agent.messages if message.get("role") == "tool"]
                    self.assertEqual(len(tool_messages), 1)
                    self.assertIn("marker.txt", tool_messages[0]["content"])
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
