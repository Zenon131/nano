
with open("/Users/jonathanwallace/Projects/nano-agent/nano_agent/core.py", "r") as f:
    content = f.read()

# Replace the broken JSON argument string
old_args = '{"command": \"find . -name \\\"*.py\\\" | wc -l > file_count.txt\"}'
new_args = '{"command": "find . -name \\"*.py\\" | wc -l > file_count.txt"}'

updated_content = content.replace(old_args, new_args)
with open("/Users/jonathanwallace/Projects/nano-agent/nano_agent/core.py", "w") as f:
    f.write(updated_content)
