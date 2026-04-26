# Build Notes

## Windows executable

The Windows executable is built by GitHub Actions with the workflow in
`.github/workflows/build-windows-exe.yml`.

The workflow:

- runs on `windows-latest`
- uses Python `3.12`
- installs `pyinstaller`, `pyserial`, and `matplotlib`
- builds `dist/WinixMonitor_v28.exe` from `winix_monitor_v28.py`
- uploads the executable as a workflow artifact

To run the build manually:

```text
GitHub -> Actions -> Build Windows EXE -> Run workflow
```

The same build command can be run on a Windows machine with Python installed:

```powershell
python -m pip install pyinstaller pyserial matplotlib
python -m PyInstaller --onefile --windowed --name WinixMonitor_v28 winix_monitor_v28.py
```

Runtime/source dependencies are listed in `README.md`.

## Code signing

The executable is currently unsigned. Signing requires a Windows code-signing
certificate from a certificate authority. For public distribution, use an OV or
EV code-signing certificate. EV certificates usually build SmartScreen reputation
faster, but they cost more and normally require a hardware token or cloud HSM.

Signing is done after PyInstaller creates the executable. On Windows, install
the Windows SDK and use `signtool.exe`:

```powershell
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a dist\WinixMonitor_v28.exe
```

If the certificate is in a `.pfx` file:

```powershell
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /f certificate.pfx /p YOUR_PASSWORD dist\WinixMonitor_v28.exe
```

The timestamp server is important. It keeps the signature valid after the
certificate expires, as long as the executable was signed while the certificate
was valid.

Do not commit private certificates or passwords to the repository. If signing is
added to GitHub Actions later, store certificate material and passwords as
GitHub Actions secrets.
