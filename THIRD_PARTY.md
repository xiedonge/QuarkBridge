# Third-party components

The login window uses unmodified dynamically linked PySide6 / Qt WebEngine 6.10.2 and Chromium components. Qt/PySide is distributed under its applicable LGPL/GPL exception or permissive component terms; no ownership over these components is claimed. Package-provided license notices are copied into the application licenses directory during the build.

- Qt for Python notices: https://doc.qt.io/qtforpython-6/licenses.html
- Qt WebEngine notices: https://doc.qt.io/qt-6/qtwebengine-licensing.html
- Corresponding PySide source: https://code.qt.io/cgit/pyside/pyside-setup.git/?h=6.10.2
- Corresponding Qt WebEngine source: https://code.qt.io/cgit/qt/qtwebengine.git/?h=v6.10.2
- Corresponding Qt modules: https://code.qt.io/cgit/qt/

The onedir application retains replaceable shared libraries; rebuilding with modified compatible libraries is supported by the public packaging scripts. Reverse engineering for debugging modifications to LGPL-covered components is not prohibited by this project.
