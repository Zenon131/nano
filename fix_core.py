
with open("nano_agent/core.py", "r") as f:
    lines = f.readlines()
for i in range(len(lines)):
    if "async def _run_goal" in lines[i]:
        lines[i] = "    async def _run_goal(self, user_input: str):\n"
with open("nano_agent/core.py", "w") as f:
    f.writelines(lines)
