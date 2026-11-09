
with open("/Users/jonathanwallace/Projects/nano-agent/nano_agent/core.py", "r") as f:
    lines = f.readlines()

# Find MockProvider class and update its chat method
new_mock_provider = [
    "class MockProvider:\n",
    "    def __init__(self):\n",
    "        self.call_count = 0\n",
    "    async def chat(self, messages):\n",
    "        self.call_count += 1\n",
    "        if self.call_count == 1:\n",
    "            return {\n",
    "                'content': 'Using tool',\n",
    "                'tool_calls': [{\n",
    "                    'id': 'call_1',\n",
    "                    'function': {'name': 'shell', 'arguments': '{\"command\": \"ls\"}'}\n",
    "                }]\n",
    "            }\n",
    "        return {'content': 'Task complete.', 'tool_calls': []}\n",
    "\n"
]

# Replace the existing MockProvider block
start_idx = -1
end_idx = -1
for i, line in enumerate(lines):
    if "class MockProvider:" in line:
        start_idx = i
    if "class ToolRegistry:" in line:
        end_idx = i
        break

if start_idx != -1 and end_idx != -1:
    lines[start_idx:end_idx] = new_mock_provider
    with open("/Users/jonathanwallace/Projects/nano-agent/nano_agent/core.py", "w") as f:
        f.writelines(lines)
