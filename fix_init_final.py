
with open("/Users/jonathanwallace/Projects/nano-agent/nano_agent/core.py", "r") as f:
    lines = f.readlines()

start_idx = -1
for i, line in enumerate(lines):
    if line.strip().startswith("def __init__"):
        start_idx = i
        break

if start_idx != -1:
    end_idx = len(lines)
    for i in range(start_idx + 1, len(lines)):
        if lines[i].startswith("    def ") or lines[i].startswith("    async def "):
            end_idx = i
            break
    
    new_init = [
        "    def __init__(self, tools_dir: Optional[str] = None, confirm_callback: Optional[Callable] = None, interactive=True, auto_approve=False):\n",
        "        self.interactive = interactive\n",
        "        self.auto_approve = auto_approve\n",
        "        self.confirm_callback = confirm_callback\n",
        "        self.registry = ToolRegistry(tools_dir)\n",
        "        self.registry.load_tools()\n",
        "        \n",
        "        # Config\n",
        "        self.base_url = os.environ.get(\"OPENAI_BASE_URL\", \"http://localhost:11434/v1\")\n",
        "        self.model = \"llama3\"\n",
        "        self.api_key = os.environ.get(\"OPENAI_API_KEY\", \"ollama\")\n",
        "        \n",
        "        self.client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key)\n",
        "        \n",
        "        # State & Session\n",
        "        self.messages = []\n",
        "        self.snapshots = SnapshotManager(\".\")\n",
        "        self.session = None\n",
        "        self.prompt_session = None\n",
        "        \n",
        "        self._register_core_tools()\n"
    ]
    
    lines[start_idx:end_idx] = new_init
    with open("/Users/jonathanwallace/Projects/nano-agent/nano_agent/core.py", "w") as f:
        f.writelines(lines)
