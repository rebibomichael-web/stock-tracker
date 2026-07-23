#!/bin/bash
# Launches the NYC DOE Record Anonymizer GUI (or forwards CLI args, e.g.
# ./run_anonymizer.sh Records.pdf --expect 133).
#
# Run this directly, or use install_desktop_shortcut.sh once to get an
# application-menu entry / Desktop icon that runs this script.
#
# The desktop entry runs with Terminal=false, so any output here is
# normally invisible to the user — a failed dependency install would just
# look like nothing happened on double-click. install_pymupdf/show_error
# below exist specifically to make a failure visible instead of silent.
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

PIP_LOG="$(mktemp -t anonymizer_pip.XXXXXX.log)"

install_pymupdf() {
    if pip3 install --user pymupdf >"$PIP_LOG" 2>&1; then
        return 0
    fi
    # PEP 668 "externally-managed-environment" systems (Debian/Ubuntu
    # 23.04+ and derivatives, by default) reject a plain --user install.
    # --break-system-packages only lifts pip's own guard rail here — it
    # still installs into the same --user site-packages, not anything
    # apt-managed.
    if grep -qi "externally-managed-environment" "$PIP_LOG"; then
        echo "System Python is externally-managed (PEP 668) — retrying " \
            "with --break-system-packages"
        pip3 install --user --break-system-packages pymupdf >>"$PIP_LOG" 2>&1
        return $?
    fi
    return 1
}

show_error() {
    local msg="$1"
    echo "$msg" >&2
    if command -v zenity >/dev/null 2>&1; then
        zenity --error --title="NYC DOE Record Anonymizer" --text="$msg" 2>/dev/null
    elif command -v kdialog >/dev/null 2>&1; then
        kdialog --error "$msg" 2>/dev/null
    else
        python3 - "$msg" <<'PYEOF' 2>/dev/null
import sys, tkinter, tkinter.messagebox
root = tkinter.Tk()
root.withdraw()
tkinter.messagebox.showerror("NYC DOE Record Anonymizer", sys.argv[1])
PYEOF
    fi
}

if ! python3 -c "import fitz" 2>/dev/null; then
    echo "PyMuPDF not installed — installing now..."
    if ! install_pymupdf; then
        show_error "Could not install PyMuPDF automatically. Open a terminal and run:

  pip3 install --user pymupdf

(add --break-system-packages if your system requires it — see
$PIP_LOG for the actual error)"
        exit 1
    fi
fi

exec python3 record_anonymizer.py "$@"
