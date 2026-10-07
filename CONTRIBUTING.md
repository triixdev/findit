# Contributing to FindIt

Thanks for helping! This project is part of Hacktoberfest.

## Quick start
1. Fork the repo and clone it.
2. Copy `token.txt.example` to `token.txt` and paste your own Hugging Face token (never commit it).
3. Run `run.bat` (Windows) or `pip install -r requirements.txt && python app.py`.
4. Make your change, test it in the browser, open a pull request.

## Good first issues
- Add a "mark as returned" button for office staff
- Show item photos (upload + store in SQLite)
- Send an email notification when a high match appears
- Add a dark/light theme toggle
- Write tests for `tokens()`, `similarity()` and `verify_and_claim()`

## Rules
- Keep the app working without a token (forms + offline mode must still work).
- Keep PRs small and describe what you tested.
