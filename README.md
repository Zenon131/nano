# 🌌 nanoc
**The Transparent Shell Agent.**

`nanoc` is a minimalist, high-agency AI agent harness designed for developers who demand total transparency. Unlike "black box" agents, `nanoc` treats the terminal as a first-class interface, streaming every thought, tool call, and result in real-time.

---

## 🚀 Quick Start

### 1. Install
Clone the repo and install as an editable package:
```bash
git clone https://github.com/Zenon131/nano.git
cd nano
pip install -e .
```

### 2. Configure
Set the environment variables needed by your provider before starting Nano. For example, OpenRouter uses `OPENROUTER_API_KEY`, OpenAI uses `OPENAI_API_KEY`, and Tavily web search optionally uses `TAVILY_API_KEY`. Do not share your keys or commit them to the repository.

Nano also supports legacy `.nano.json` API keys, which are stored in plaintext; environment variables are recommended.

### 3. Run
Launch the agent from anywhere in your terminal:
```bash
nanoc --new
```

---

## 🧠 Core Philosophy

`nanoc` is built on three pillars of agentic design:

### 1. Total Transparency
No hidden loops. Every interaction is rendered using `rich` panels:
- **Cyan Panels**: Tool calls and arguments.
- **Green Panels**: Tool outputs and results.
- **Telemetry**: Toggle the `/debug` window to see the raw JSON payloads being sent to the LLM.

### 2. Zero-Config Extensibility (Folder-as-Plugin)
Adding capabilities to `nanoc` requires no registration or boilerplate.
- **The Rule**: Any Python function placed in the `tools/` directory is automatically discovered via reflection.
- **Requirements**: Provide **type hints** and a **docstring**. The docstring becomes the tool's description in the LLM's system prompt.

**Example (`tools/github.py`):**
```python
def create_issue(title: str, body: str):
    """Creates a new issue on the current GitHub repository."""
    # Implementation here...
    return "Issue #123 created successfully."
```

### 3. Recursive Agency
`nanoc` can delegate. When faced with a complex, multi-step problem, the agent can use the `spawn_sub_agent` tool to create a specialized, isolated instance of itself. This prevents "context drift" and allows for parallel reasoning.

---

## 🛠️ Capabilities

### 🔋 Built-in Powers
Out of the box, `nanoc` is equipped to:
- **Web Search**: Use Tavily to find current news, documentation, and real-time data.
- **Explore**: List directories and read files across your system.
- **Manipulate**: Create new files (`write_file`) and surgically edit existing ones (`edit_file`).
- **Execute**: Run shell commands via `run_command`.
- **Delegate**: Spawn specialized sub-agents for complex, multi-step reasoning.

### ⚡ Slash Commands
Manage your agent's state without leaving the chat:

| Command | Description |
| :--- | :--- |
| `/help` | Show this list of available commands |
| `/provider` or `/provider <name>` | Choose a provider; each retains its own endpoint, model and credential settings |
| `/model` or `/model <model-id>` | Choose a model for the active provider; discovers installed Ollama models |
| `/config` | Update the active provider's base URL, key environment-variable name, or model |
| `/save <name>` | Archive the current conversation as a named session |
| `/sessions` | List and teleport between past archived sessions |
| `/context` | View all context imported from other agents (Hermes, OpenCode, etc.) |
| `/debug` | Toggle the "Tool Calls & Telemetry" detail panel |

### 📁 Session & Context Hub
`nanoc` acts as a central hub for your AI history:
- **Session Library**: All conversations are stored in `~/.nano/sessions/`. You can switch between them instantly using `/sessions`.
- **Smart Import**: On startup, `nanoc` scans your system for history files from other agents (Hermes, OpenCode, OpenClaw). If it finds a compatible chat log, it imports it into the current session's context automatically.

### 🛡️ Safety Gates
Tools can be marked unsafe with `setattr(my_tool, "unsafe", True)` at module level, before Nano discovers them. Unsafe built-ins such as `run_command`, `write_file`, and `edit_file` require approval in the CLI; if no confirmation callback is available, Nano denies execution. Sub-agents inherit the parent agent's confirmation callback.

---

## ⚙️ Optimal Setup

### Choose a provider and model
Nano has presets for **OpenRouter**, **OpenAI**, **Ollama** and a **custom** OpenAI-compatible API. Each provider keeps its own model and endpoint in `.nano.json` in the directory where you launch Nano; switching providers restores those settings. Existing single-provider settings are migrated when you first change a provider or model.

When switching to a different endpoint, Nano asks for confirmation: subsequent requests can include the current conversation, system prompt, and tool results. Changing an endpoint also warns that the provider's API key may be sent to that address. Declining leaves your settings unchanged. Provider updates require `.nano.json` to be a regular file, not a symlink; Nano coordinates its own concurrent writers, but not unrelated programs editing that file.

For OpenRouter, set `OPENROUTER_API_KEY` in your shell before launching `nanoc`, then enter these commands inside Nano:

```text
/provider openrouter
/model google/gemma-4-31b-it
```

The exact model ID stays paired with OpenRouter's `https://openrouter.ai/api/v1` endpoint. Use `/provider ollama` to switch back to Ollama, and `/model` to select an installed Ollama model. Ollama may also offer cloud models; choose a local model if you need local-only inference.

For another OpenAI-compatible endpoint, select `/provider custom`, use `/config` → `url` to set its base URL, and use `/config` → `key` to specify the **name** of an environment variable containing its API key (default `CUSTOM_API_KEY`). Set that variable before starting Nano, then enter `/model <exact-model-id>`. The `/provider` and `/model` commands also offer interactive menus when run without arguments.

### Project-Specific Personality
Drop a `.nano.json` file into any project folder to define a project-specific brain:
```json
{
  "model": "gpt-4o",
  "system_prompt": "You are an expert Rust engineer specializing in async runtimes.",
  "tools_dir": "./custom_tools"
}
```
When launched from that directory, `nanoc` adopts these settings automatically.

---

## ⌨️ CLI Options
- `nanoc`: Starts the agent with the last saved session.
- `nanoc --new`: Wipes the current session and starts fresh.
