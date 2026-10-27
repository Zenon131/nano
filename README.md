# 🌌 Nano
**The Transparent Shell Agent.**

Nano is a minimalist, open-source AI agent harness that lives in your terminal. Unlike "black box" agents, Nano is completely transparent: you see every thought, every tool call, and every result in real-time.

## 🚀 Quick Start (2 Minutes)

### 1. Install
Clone the repo and install as an editable package:
```bash
git clone https://github.com/yourusername/nano.git
cd nano
pip install -e .
```

### 2. Configure
Create a `.env` file in the root directory:
```bash
OPENAI_API_KEY=your_api_key_here
```

### 3. Run
Since you installed it as a package, you can now run Nano from **anywhere**:
```bash
nano
```

---

## 🧠 Model & Provider Management

Nano is provider-agnostic. It works with OpenAI, any OpenAI-compatible API, and local models via Ollama.

### ⚡ Slash Commands
While chatting, use these commands to change your agent's brain:

*   `/model`: Opens a visual selector. It automatically discovers your local Ollama models and lists your configured custom models.
*   `/config`: Opens the configuration menu to update your **Base API URL**, **API Key**, or add **Manual Models**.

### 🏠 Local Models (Ollama)
To run Nano 100% locally, ensure Ollama is running and use `/model` to select a local model. Nano will automatically configure the endpoint to `http://localhost:11434/v1`.

### 📁 Project-Specific Context
Drop a `.nano.json` into any project folder. Nano will automatically detect it and adopt the project's personality, model, and toolset.

---

## 🛠️ Capabilities & Extensibility

### 🔋 Built-in Powers
Out of the box, Nano can:
*   **Explore**: List directories and read files.
*   **Manipulate**: Create new files (`write_file`) and surgically edit existing ones (`edit_file`).
*   **Execute**: Run shell commands via `run_command`.
*   **Delegate**: Spawn specialized sub-agents for complex, multi-step reasoning.

### 🔌 Adding Your Own Tools
Nano's extensibility is "zero-config." To give Nano a new power:
1. Create a `.py` file in the `tools/` directory.
2. Write a Python function with **type hints** and a **docstring**.
3. Restart Nano.

**Example (`tools/weather.py`):**
```python
def get_weather(city: str):
    \"\"\"Fetches the current weather for a given city.\"\"\"
    # Your API call here
    return f\"The weather in {city} is sunny and 75°F.\"
```

---

## 🛡️ Safety & Transparency
*   **Transparent Execution**: Every tool call is rendered in a distinct cyan panel; every result in a green panel.
*   **Safety Gates**: Any tool marked as `unsafe` (like `run_command`, `write_file`, or `edit_file`) will trigger a manual confirmation prompt before execution.
*   **Recursive Intelligence**: Nano can spawn \"sub-agents\" to handle complex sub-tasks independently.
*   **Session Memory**: Conversations are persisted in `.nano_history` so you can pick up where you left off.

## ⌨️ Commands
*   `exit` / `quit`: Stop the agent.
*   `/model`: Change the active AI model.
*   `/config`: Update API keys and endpoints.
*   `nano --new`: Start a fresh session (wipes history).

---

## 💡 Pro-Tip
If you didn't use `pip install -e .`, you can still run Nano by adding this alias to your `.zshrc` or `.bashrc`:
```bash
alias nano='export PYTHONPATH=$PYTHONPATH:/absolute/path/to/nano && python3 -m nano_agent.cli'
```
