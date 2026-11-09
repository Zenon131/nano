
import os
from pathlib import Path

with open("/Users/jonathanwallace/Projects/nano-agent/nano_agent/core.py", "r") as f:
    content = f.read()

# Add imports
if "from nano_agent.providers import" not in content:
    content = "from nano_agent.providers import ProviderSettings, OpenAICompatibleProvider\n" + content

# Replace __init__
import re
# Find the __init__ block
init_pattern = r"    def __init__\(.*?\):.*?self\._register_core_tools\(\)"
new_init = """    def __init__(self, tools_dir: Optional[str] = None, confirm_callback: Optional[Callable] = None, interactive=True, auto_approve=False):
        self.interactive = interactive
        self.auto_approve = auto_approve
        self.confirm_callback = confirm_callback
        self.registry = ToolRegistry(tools_dir)
        self.registry.load_tools()
        
        # Config
        config = self._load_config()
        self.settings = ProviderSettings(config)
        self.provider = OpenAICompatibleProvider(self.settings)
        self.model = self.settings.current["model"]
        
        self.messages = []
        self.snapshots = SnapshotManager(".")
        self.session = SessionManager()
        self.prompt_session = None
        self._register_core_tools()"""
content = re.sub(init_pattern, new_init, content, flags=re.DOTALL)

# Insert _load_config
config_method = """
    def _load_config(self):
        config_path = Path.home() / ".nano.json"
        if config_path.exists():
            try:
                import json
                return json.loads(config_path.read_text())
            except:
                pass
        return {}
"""
content = content.replace("    def _register_core_tools(self):", config_method + "\n    def _register_core_tools(self):")

with open("/Users/jonathanwallace/Projects/nano-agent/nano_agent/core.py", "w") as f:
    f.write(content)
print("Success")
