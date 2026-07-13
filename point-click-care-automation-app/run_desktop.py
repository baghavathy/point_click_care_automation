"""Entry point for the packaged Gateway PCC desktop agent (.exe).

PyInstaller bundles this; at runtime it starts the local desktop server, which
opens the UI in the user's default browser. The server to connect to is chosen
on the sign-in screen (and remembered).
"""
from backend.desktop import main

if __name__ == "__main__":
    main()
