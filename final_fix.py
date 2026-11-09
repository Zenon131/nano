
with open("/Users/jonathanwallace/Projects/nano-agent/nano_agent/core.py", "r") as f:
    lines = f.readlines()
with open("/Users/jonathanwallace/Projects/nano-agent/nano_agent/core.py.tmp", "w") as f:
    for line in lines:
        f.write(line.rstrip() + "\n")
import os
os.replace("/Users/jonathanwallace/Projects/nano-agent/nano_agent/core.py.tmp", "/Users/jonathanwallace/Projects/nano-agent/nano_agent/core.py")
