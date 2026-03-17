#!/usr/bin/env python3
"""
Run the Omaha Oracle Streamlit dashboard locally.

  python run_dashboard.py

Or with streamlit directly:
  PYTHONPATH=src streamlit run src/dashboard/app.py
"""
import os
import sys

# Add src to path
src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if src not in sys.path:
    sys.path.insert(0, src)

os.chdir(os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    import streamlit.web.cli as stcli

    app_path = os.path.join(os.path.dirname(__file__), "src", "dashboard", "app.py")
    sys.argv = ["streamlit", "run", app_path]
    stcli.main()
