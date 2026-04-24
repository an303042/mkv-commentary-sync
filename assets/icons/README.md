Place the app branding files here:

- `app_icon.png`: used for the window icon when running `python main.py` and in packaged builds.
- `app_icon.ico`: embedded into the Windows `.exe` during `build.bat` and GitHub Actions builds.
- `app_icon.icns`: optional future macOS app-bundle icon.

The current release workflow already uses `mkvsyncdub.spec`, so once these files are present the local Windows build and the GitHub release builds will pick them up automatically.

GitHub repository branding is separate from the codebase:

- Repo avatar / org avatar: set in GitHub settings.
- Social preview image: set in repository `Settings -> General -> Social preview`.
- README image: can reference `assets/icons/app_icon.png` once that file is committed.
