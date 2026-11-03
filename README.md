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
Create a `.env` file in the root directory or set your environment variables:
```bash
OPENAI_API_KEY=your_api_key_here
```

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
    \"\"\"Creates a new issue on the current GitHub repository.\"\"\"
    # Implementation here...
    return "Issue #123 created successfully."
```

### 3. Recursive Agency
`nanoc` can delegate. When faced with a complex, multi-step problem, the agent can use the `spawn_sub_agent` tool to create a specialized, isolated instance of itself. This prevents "context drift" and allows for parallel reasoning.

---

## 🛠️ Advanced Usage

### ⚡ Slash Commands
Manage your agent's state without leaving the chat:

| Command | Description |
| :--- | :--- |
| `/help` | Show this list of available commands |
| `/model` | Switch AI models (Auto-discovers local Ollama models) |
| `/config` | Update API Base URL, Keys, or add Custom Models |
| `/save <name>` | Archive the current conversation as a named session |
| `/sessions` | List and teleport between past archived sessions |
| `/context` | View all context imported from other agents (Hermes, OpenCode, etc.) |
| `/debug` | Toggle the "Tool Calls & Telemetry" detail panel |

### 📁 Session & Context Hub
`nanoc` acts as a central hub for your AI history:
- **Session Library**: All conversations are stored in `~/.nano/sessions/`. You can switch between them instantly using `/sessions`.
- **Smart Import**: On startup, `nanoc` scans your system for history files from other agents (Hermes, OpenCode, OpenClaw). If it finds a compatible chat log, it imports it into the current session's context automatically.

### 🛡️ Safety Gates
Tools can be marked as `unsafe = True` in their schema. When an unsafe tool (like `run_command` or `write_file`) is called, `nanoc` pauses execution and requires a manual **Y/N** confirmation from the user.

---

## ⚙️ Optimal Setup

### Local-First (Ollama)
To run `nanoc` 100% locally:
1. Start Ollama.
2. Run `nanoc`.
3. Use `/model` to select your local model. `nanoc` will automatically route requests to `http://localhost:11434/v1`.

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
