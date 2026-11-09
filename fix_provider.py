
with open("/Users/jonathanwallace/Projects/nano-agent/nano_agent/core.py", "r") as f:
    lines = f.readlines()

# We want to replace the loop from line 118 to 141
new_run_loop = [
    "        while True:\n",
    "            response = await self.provider.chat(self.messages)\n",
    "            self.messages.append(response)\n",
    "            \n",
    "            if response.get('tool_calls'):\n",
    "                for tc in response['tool_calls']:\n",
    "                    name = tc['function']['name']\n",
    "                    args = json.loads(tc['function']['arguments'])\n",
    "                    schema = next((s for s in self.registry.schemas if s['function']['name'] == name), {})\n",
    "                    if schema.get('unsafe', False) and not self.auto_approve:\n",
    "                        if self.confirm_callback and not await self.confirm_callback(name, args):\n",
    "                            res = 'Error: Denied.'\n",
    "                        else:\n",
    "                            self.snapshots.snapshot(label=name)\n",
    "                            res = await self.registry.execute(name, args)\n",
    "                    else:\n",
    "                        res = await self.registry.execute(name, args)\n",
    "                    self.messages.append({'role': 'tool', 'tool_call_id': tc['id'], 'name': name, 'content': res})\n",
    "                continue\n",
    "            return response['content']\n"
]

# Find the loop start and end
start_idx = -1
end_idx = -1
for i, line in enumerate(lines):
    if "while True:" in line and "self.client.chat.completions.create" in lines[i+1]:
        start_idx = i
    if "return msg.content" in line:
        end_idx = i + 1

if start_idx != -1 and end_idx != -1:
    lines[start_idx:end_idx] = new_run_loop
    
    # Also update __init__ to use MockProvider and remove self.client
    for i in range(len(lines)):
        if "self.client = AsyncOpenAI" in lines[i]:
            lines[i] = "        self.provider = MockProvider()\n"
    
    with open("/Users/jonathanwallace/Projects/nano-agent/nano_agent/core.py", "w") as f:
        f.writelines(lines)
