
with open("/Users/jonathanwallace/Projects/nano-agent/nano_agent/core.py", "r") as f:
    content = f.read()

# Look for the argument string and replace it with something very simple.
import re
content = re.sub(r"\'arguments\':\s*\'\{.*?\}\'", "'arguments': '{\"command\": \"ls\"}'", content)

with open("/Users/jonathanwallace/Projects/nano-agent/nano_agent/core.py", "w") as f:
    f.write(content)
