
with open("/Users/jonathanwallace/Projects/nano-agent/nano_agent/core.py", "r") as f:
    lines = f.readlines()

# Find the index of the method
start_idx = -1
for i, line in enumerate(lines):
    if "async def _handle_command(self, cmd: str):" in line:
        start_idx = i
        break

if start_idx != -1:
    # Find the end of the method (the next line that starts with a non-indented def or class)
    end_idx = len(lines)
    for i in range(start_idx + 1, len(lines)):
        if lines[i].startswith("class ") or (lines[i].startswith("def ") and not lines[i].startswith("    def ")):
            end_idx = i
            break
    
    new_method = [
        "    async def _handle_command(self, cmd: str):\n",
        "        parts = cmd.split()\n",
        "        action = parts[0].lower()\n",
        "        \n",
        "        if action == \"/help\":\n",
        "            return \"Nano Commands:\\n/undo - Restore last snapshot\\n/clear - Clear session\\n/auto - Toggle auto-approve\\n/save - Manual snapshot\"\n",
        "        \n",
        "        if action == \"/undo\":\n",
        "            if not self.snapshots.history:\n",
        "                return \"No snapshots available.\"\n",
        "            self.snapshots.restore(self.snapshots.history[0])\n",
        "            return \"Workspace restored to last snapshot.\"\n",
        "            \n",
        "        if action == \"/clear\":\n",
        "            self.messages = []\n",
        "            return \"Session cleared.\"\n",
        "            \n",
        "        if action == \"/auto\":\n",
        "            self.auto_approve = not self.auto_approve\n",
        "            status = \"enabled\" if self.auto_approve else \"disabled\"\n",
        "            return f\"Auto-approve is now {status}.\"\n",
        "\n",
        "        if action == \"/save\":\n",
        "            self.snapshots.snapshot(label=\"Manual Save\")\n",
        "            return \"Manual snapshot created.\"\n",
        "            \n",
        "        return f\"Unknown command: {action}\"\n"
    ]
    
    lines[start_idx:end_idx] = new_method
    with open("/Users/jonathanwallace/Projects/nano-agent/nano_agent/core.py", "w") as f:
        f.writelines(lines)
    print("Successfully restored slash commands.")
else:
    print("Could not find _handle_command")
