"""Quick smoke test: can WatsonX Maverick generate Python code blocks (CodeAct)?"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# Minimal prompt mimicking CodeAct system instructions
SYSTEM = """You are a coding assistant. You have these tools available as async Python functions:

### `execute_command(command: str)`
Run a shell command and return the output.

### `write_file(path: str, content: str)`
Write content to a file at the given path.

When you want to use a tool, reply with ONLY a fenced Python code block:
```python
result = await execute_command("echo hello")
print(result)
```
Do NOT include any text outside the code block."""

USER = "Create a file called hello.py that prints 'Hello World', then run it."


def main():
    api_key = os.environ.get("WATSONX_API_KEY")
    project_id = os.environ.get("WATSONX_PROJECT_ID")

    if not api_key or not project_id:
        print("SKIP: WATSONX_API_KEY or WATSONX_PROJECT_ID not set")
        return 0

    print(f"WatsonX API key: ...{api_key[-6:]}")
    print(f"WatsonX project: {project_id}")

    from langchain_ibm import ChatWatsonx

    model = ChatWatsonx(
        model_id="meta-llama/llama-4-maverick-17b-128e-instruct-fp8",
        temperature=0.1,
        max_tokens=2000,
        project_id=project_id,
    )

    print("\nSending prompt to WatsonX Maverick...")
    from langchain_core.messages import HumanMessage, SystemMessage

    resp = model.invoke([SystemMessage(content=SYSTEM), HumanMessage(content=USER)])

    text = resp.content
    print(f"\n{'=' * 60}")
    print("MODEL RESPONSE:")
    print(f"{'=' * 60}")
    print(text)
    print(f"{'=' * 60}")

    # Check for Python code block
    import re

    blocks = re.findall(r"```python(.*?)```", text, re.DOTALL)
    if blocks:
        print(f"\n✅ Found {len(blocks)} Python code block(s) — CodeAct works!")
        for i, b in enumerate(blocks):
            print(f"\n--- Block {i + 1} ---")
            print(b.strip())
        return 0
    else:
        print("\n❌ No Python code blocks found — CodeAct may not work with this model")
        return 1


if __name__ == "__main__":
    sys.exit(main())
