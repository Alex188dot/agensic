import os
import json
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from litellm import completion
import logging

# Configuration paths
CONFIG_DIR = os.path.expanduser("~/.termimind")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
LOG_FILE = os.path.join(CONFIG_DIR, "server.log")

# Setup Logging
logging.basicConfig(filename=LOG_FILE, level=logging.INFO, format='%(asctime)s - %(message)s')

app = FastAPI()

class Context(BaseModel):
    command_buffer: str
    cursor_position: int
    working_directory: str
    shell: str

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

@app.get("/health")
def health():
    return {"status": "ok", "provider": load_config().get("provider", "unknown")}

@app.post("/predict")
def predict_completion(ctx: Context):
    config = load_config()
    
    model = config.get("model", "gpt-3.5-turbo")
    provider = config.get("provider", "openai")
    api_key = config.get("api_key", None)
    base_url = config.get("base_url", None)

    # --- OLLAMA SPECIFIC FIXES ---
    if provider == "ollama":
        # Litellm expects "ollama/modelname"
        if not model.startswith("ollama/"):
            model = f"ollama/{model}"
        # If no base_url provided, assume default local ollama
        if not base_url:
            base_url = "http://localhost:11434"

    system_prompt = (
        "You are a smart terminal autocompletion engine. "
        "Output ONLY the text needed to complete the command. "
        "Do not repeat the input. No markdown. "
        f"Context: CWD={ctx.working_directory}, Shell={ctx.shell}."
    )

    user_message = f"Input: '{ctx.command_buffer}'"
    logging.info(f"Request: {user_message} | Model: {model}")

    try:
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            "max_tokens": 60,
            "temperature": 0.1,
            "drop_params": True # Helps with provider compatibility
        }
        
        if api_key: kwargs["api_key"] = api_key
        if base_url: kwargs["api_base"] = base_url # Litellm uses api_base, not base_url

        response = completion(**kwargs)
        suggestion = response.choices[0].message.content.strip()
        
        # Cleanup: If AI repeats the user input, remove it
        if suggestion.startswith(ctx.command_buffer):
            suggestion = suggestion[len(ctx.command_buffer):]
            
        logging.info(f"Suggestion: {suggestion}")
        return {"suggestion": suggestion}

    except Exception as e:
        logging.error(f"Error: {e}")
        return {"suggestion": ""}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=22000, log_level="info")