"""genvault v2 entry point: uvicorn on 127.0.0.1, port from config.json."""
import uvicorn

from backend import config

if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="127.0.0.1", port=config.PORT, log_level="info")
