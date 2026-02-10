import os
import json
import uvicorn
import re
from fastapi import FastAPI
from pydantic import BaseModel
from litellm import completion
import logging

# Configuration paths
CONFIG_DIR = os.path.expanduser("~/.termimind")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
LOG_FILE = os.path.join(CONFIG_DIR, "server.log")

# Setup Logging
logging.basicConfig(
    filename=LOG_FILE, 
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

app = FastAPI()

class Context(BaseModel):
    command_buffer: str
    cursor_position: int
    working_directory: str
    shell: str

def load_config():
    if not os.path.exists(CONFIG_FILE):
        logging.warning("Config file not found!")
        return {}
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

@app.post("/predict")
def predict_completion(ctx: Context):
    logging.info(f"=== NEW REQUEST ===")
    logging.info(f"Buffer: '{ctx.command_buffer}'")
    logging.info(f"Cursor: {ctx.cursor_position}")
    logging.info(f"Dir: {ctx.working_directory}")
    
    config = load_config()
    model = config.get("model", "gpt-3.5-turbo")
    provider = config.get("provider", "openai")
    api_key = config.get("api_key", None)
    base_url = config.get("base_url", None)

    logging.info(f"Provider: {provider}, Model: {model}")
    logging.info(f"API Key present: {bool(api_key)}")
    logging.info(f"Base URL: {base_url}")

    # Fix model names for different providers
    if provider == "groq":
        # Groq models should NOT have the groq/ prefix doubled
        if model.startswith("groq/openai/"):
            model = "groq/openai/gpt-oss-20b"
            logging.info(f"Corrected Groq model to: {model}")
        elif not model.startswith("groq/"):
            model = f"groq/{model}"
            logging.info(f"Added groq/ prefix: {model}")
    
    elif provider == "ollama":
        if not model.startswith("ollama/"):
            model = f"ollama/{model}"
        if not base_url:
            base_url = "http://localhost:11434"
    
    elif provider == "anthropic":
        if not model.startswith("claude"):
            model = f"claude-3-5-sonnet-20241022"
    
    elif provider == "gemini":
        if not model.startswith("gemini/"):
            model = f"gemini/{model}"

    # STRICTER PROMPT
    system_prompt = (
        "You are a terminal autocomplete. "
        "User typed a partial command. "
        "Complete ONLY the rest - do NOT repeat what they typed. "
        "No markdown, no explanations, just the completion.\n"
        "Examples:\n"
        "Input: 'git com' → 'mit -m \"\"'\n"
        "Input: 'docker run ' → '-it ubuntu'\n"
        "Input: 'pip install ' → 'requests'"
    )

    try:
        # Build kwargs for litellm
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Complete: {ctx.command_buffer}"}
            ],
            "temperature": 0.1,
        }
        
        # Set API key if provided
        if api_key:
            # For Groq, set the GROQ_API_KEY environment variable
            if provider == "groq":
                os.environ["GROQ_API_KEY"] = api_key
                logging.info("Set GROQ_API_KEY environment variable")
            elif provider == "openai":
                os.environ["OPENAI_API_KEY"] = api_key
            elif provider == "anthropic":
                os.environ["ANTHROPIC_API_KEY"] = api_key
            elif provider == "gemini":
                os.environ["GEMINI_API_KEY"] = api_key
            else:
                kwargs["api_key"] = api_key
        
        # Set base URL for local models
        if base_url:
            kwargs["api_base"] = base_url
            logging.info(f"Using api_base: {base_url}")

        logging.info(f"Calling LLM with model: {model}")
        logging.info(f"Messages: {kwargs['messages']}")
        
        response = completion(**kwargs)
        
        logging.info(f"LLM Response object: {response}")
        
        raw_suggestion = response.choices[0].message.content.strip()
        
        logging.info(f"Raw AI response: '{raw_suggestion}'")

        # CLEANUP: Remove any markdown formatting
        clean_suggestion = re.sub(r"```.*?```", "", raw_suggestion, flags=re.DOTALL)
        clean_suggestion = clean_suggestion.replace("```", "").strip()
        
        # Remove quotes if AI wrapped the response
        if clean_suggestion.startswith('"') and clean_suggestion.endswith('"'):
            clean_suggestion = clean_suggestion[1:-1]
        if clean_suggestion.startswith("'") and clean_suggestion.endswith("'"):
            clean_suggestion = clean_suggestion[1:-1]

        input_cmd = ctx.command_buffer
        
        # OVERLAP REMOVAL: If AI repeated the input, extract only the new part
        if clean_suggestion.startswith(input_cmd):
            final_suggestion = clean_suggestion[len(input_cmd):]
            logging.info(f"Removed overlap, extracted: '{final_suggestion}'")
        else:
            final_suggestion = clean_suggestion

        logging.info(f"FINAL SUGGESTION: '{final_suggestion}' (length: {len(final_suggestion)})")
        
        return {"suggestion": final_suggestion}

    except Exception as e:
        logging.error(f"ERROR: {str(e)}", exc_info=True)
        return {"suggestion": "", "error": str(e)}

if __name__ == "__main__":
    logging.info("Starting TermiMind server on port 22000...")
    uvicorn.run(app, host="127.0.0.1", port=22000, log_level="info")