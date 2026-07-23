#!/bin/bash
# Launches the NYC DOE Record Anonymizer GUI (or forwards CLI args, e.g.
# ./run_anonymizer.sh Records.pdf --expect 133).
#
# Run this directly, or use install_desktop_shortcut.sh once to get an
# application-menu entry / Desktop icon that runs this script.
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if ! python3 -c "import fitz" 2>/dev/null; then
    echo "PyMuPDF not installed — installing now (pip3 install --user pymupdf)..."
    pip3 install --user pymupdf
fi

exec python3 record_anonymizer.py "$@"
