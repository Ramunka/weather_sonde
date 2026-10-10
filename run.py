"""Development entry point. In production the supervisor starts the app via `flask run`."""
import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    # Never enable debug on a network-reachable host: the Werkzeug debugger allows code execution.
    app.run(host=os.getenv("FLASK_RUN_HOST", "127.0.0.1"),
            port=int(os.getenv("FLASK_RUN_PORT", "5000")),
            debug=os.getenv("FLASK_DEBUG") == "1")
