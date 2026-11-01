#!/usr/bin/env bash
# Rerun everything: install -> rebuild data_rag -> clear index caches -> tests -> start server + open browser
set -e
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8

# Use the newest Python 3 available (3.10+ preferred; macOS's built-in python3 is 3.9)
PY=""
for v in 3.14 3.13 3.12 3.11 3.10; do command -v "python$v" >/dev/null 2>&1 && { PY="python$v"; break; }; done
[ -z "$PY" ] && PY=python3
echo "[1/6] Python: $($PY --version)  -> virtual environment .venv"
if [ -d .venv ] && [ "$(.venv/bin/python -c 'import sys;print(sys.version_info[:2])' 2>/dev/null)" != "$($PY -c 'import sys;print(sys.version_info[:2])')" ]; then
  rm -rf .venv   # .venv was made with another Python version
fi
[ -d .venv ] || "$PY" -m venv .venv
source .venv/bin/activate

echo "[2/6] Install dependencies"
python -m pip install -q --upgrade pip
grep -v -i "google-genai" requirements.txt > .venv/requirements-core.txt
python -m pip install -q -r .venv/requirements-core.txt pypdf pdfplumber pypdfium2
python -m pip install -q "google-genai" \
  || echo "    (google-genai ติดตั้งไม่ได้ในไพธอนเวอร์ชันนี้ -> ระบบใช้ OpenAI หรือการค้นคืนภายในแทน ยังทำงานได้)"

echo "[3/6] Rebuild data_rag from data/ (Fields_4.0.csv + IBSL PDF)"
python scripts/build_rag_dataset.py
python scripts/build_ibsl_dataset.py | tail -n 15

echo "[4/6] Clear index caches (database.sqlite is kept)"
rm -f storage/ibsl_field_index_cache.pkl storage/ibsl_index_cache.pkl storage/ibsl_eval_result.json

echo "[5/6] Unit tests"
python -m unittest discover -s tests -v

echo "[6/6] Start server -> http://localhost:${PORT:-8000}  (Ctrl+C to stop)"
python scripts/open_when_ready.py &
python run_server.py
