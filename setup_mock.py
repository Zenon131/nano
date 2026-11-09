
with open("/Users/jonathanwallace/Projects/nano-agent/nano_agent/core.py", "r") as f:
    lines = f.readlines()

mock_provider_code = [
    "class MockProvider:\n",
    "    async def chat(self, messages):\n",
    "        return {\n",
    "            'content': 'Using tool',\n",
    "            'tool_calls': [{\n",
    "                'id': 'call_1',\n",
    "                'function': {'name': 'shell', 'arguments': '{\"command\": \"find . -name \\\"*.py\\\" | wc -l > file_count.txt\"}'}\n",
    "            }]\n",
    "        }\n",
    "\n"
]

insert_idx = 0
for i, line in enumerate(lines):
    if "class ToolRegistry:" in line:
        insert_idx = i
        break

lines[insert_idx:insert_idx] = mock_provider_code

for i in range(len(lines)):
    if "self.provider = None" in lines[i]:
        lines[i] = "        self.provider = MockProvider()\n"
    if "self._initialize_provider()" in lines[i]:
        lines[i] = "        # self._initialize_provider()\n"

with open("/Users/jonathanwallace/Projects/nano-agent/nano_agent/core.py", "w") as f:
    f.writelines(lines)
