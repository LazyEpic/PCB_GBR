# main.py
# Unified entry point
# Replaces old console main
# Launches Qt UI

import sys

try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    print("ERROR: PySide6 not installed")
    print("Install with: pip install PySide6")
    sys.exit(1)

from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
