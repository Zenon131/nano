
with open("/Users/jonathanwallace/Projects/nano-agent/nano_agent/core.py", "r") as f:
    lines = f.readlines()

# 1. Add self.unsafe_tools = set() to ToolRegistry.__init__
for i in range(len(lines)):
    if "self.schemas = []" in lines[i]:
        lines.insert(i + 1, "        self.unsafe_tools = set()\n")
        break

# 2. Update generate_tool_schema to use self.unsafe_tools
# Find the line "unsafe": getattr(func, "unsafe", False)
for i in range(len(lines)):
    if '"unsafe": getattr(func, "unsafe", False)' in lines[i]:
        lines[i] = '            "unsafe": func.__name__ in self.unsafe_tools\n'

# 3. Update _register_core_tools to use self.registry.unsafe_tools.add()
# We need to remove the .unsafe = True lines and add the .add() calls.
for i in range(len(lines)):
    if ".unsafe = True" in lines[i]:
        # Find the function name. It's usually on the line above or inferred.
        # For simplicity, I'll use a regex to find the function name.
        import re
        # This is tricky. I'll just look for the function name in the previous lines.
        # Actually, I'll just replace the lines.
        
        # Let's find the function name by looking backwards for "async def [name]"
        func_name = "unknown"
        for j in range(i-1, -1, -1):
            if "async def " in lines[j] or "def " in lines[j]:
                match = re.search(r"def\s+(\w+)", lines[j])
                if match:
                    func_name = match.group(1)
                    break
        
        lines[i] = f"        self.registry.unsafe_tools.add('{func_name}')\n"

with open("/Users/jonathanwallace/Projects/nano-agent/nano_agent/core.py", "w") as f:
    f.writelines(lines)
