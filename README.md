# 🌌 Nano
**The Transparent Shell Agent.**

Nano is a minimalist, open-source AI agent harness that lives in your terminal. Unlike "black box" agents, Nano is completely transparent: you see every thought, every tool call, and every result in real-time.

## 🚀 Quick Start (2 Minutes)

### 1. Install
Clone the repo and install the dependencies:
```bash
git clone https://github.com/yourusername/nano.git
cd nano
pip install -r requirements.txt
```

### 2. Configure
Create a `.env` file in the root directory:
```bash
OPENAI_API_KEY=your_api_key_here
```

### 3. Run
Launch Nano and start chatting:
```bash
export PYTHONPATH=$PYTHONPATH:$(pwd)
python3 -m nano_agent.cli
```

---

## 🛠️ Advanced Usage

### 🏠 Local Models (Ollama)
Want to run Nano 100% locally? Create a `.nano.json` file in your current directory:
```json
{
  "model": "llama3",
  "base_url": "http://localhost:11434/v1",
  "api_key": "ollama",
  "system_prompt": "You are Nano, running locally. Be concise.",
  "tools_dir": "tools"
}
```

### 📁 Project-Specific Context
Drop a `.nano.json` into any project folder. Nano will automatically detect it and adopt the project's personality, model, and toolset.

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
*   **Safety Gates**: Any tool marked as `unsafe` (like `run_command`) will trigger a manual confirmation prompt before execution.
*   **Recursive Intelligence**: Nano can spawn "sub-agents" to handle complex sub-tasks independently.

## ⌨️ Commands
*   `exit` / `quit`: Stop the agent.
*   `python3 -m nano_agent.cli --new`: Start a fresh session (wipes history).

---

## 💡 Pro-Tip
To make Nano a global command, add this alias to your `.zshrc` or `.bashrc`:
```bash
alias nano='export PYTHONPATH=$PYTHONPATH:/absolute/path/to/nano && python3 -m nano_agent.cli'
```
