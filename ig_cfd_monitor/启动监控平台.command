#!/bin/bash
set -e
cd -- "$(dirname -- "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python manage.py install
open http://127.0.0.1:8787/
open http://127.0.0.1:8786/
