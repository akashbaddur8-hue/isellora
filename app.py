"""
iSellora Application Entrypoint
Allows running 'python app.py' directly from the project root.
"""
import os
import sys

# Ensure current directory is in sys.path
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from backend.app import app

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
