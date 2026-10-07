# FindIt - Campus Lost & Found AI Agent

A small AI agent that connects people who lost something with the item someone found.
Built for the MLH Hacktoberfest hackathon with **open-source models on Hugging Face**.

## How it works
- **Node X** is a lost complaint (what the owner reports). **Node Y** is a found item (what the finder reports).
- The **AI's job** is to read both and decide if they are the same thing: yes / maybe / no, a confidence, and a one-line reason. No embeddings.
- The **match graph** draws a line from each complaint to each found item the AI links. Click a line to read why.
- **Chat agent (Findy):** talk normally; it saves reports, searches, and starts claims by calling tools.
- **Ownership is plain code, not AI.** The finder's hidden detail is checked by a fixed rule (3 attempts). The secret is never sent to the AI, and contact details are released only after a correct answer.
- If the AI or internet is down, the forms and keyword matching still work.

## Run it
1. Create a free token at https://huggingface.co/settings/tokens (allow **Inference Providers**).
2. Double-click `run.bat`. The first time it asks you to paste the token and saves it to `token.txt` (git ignores this file).
3. The browser opens at http://127.0.0.1:7860. If the AI sleeps, double-click `check_ai.bat`.

Not on Windows: `pip install -r requirements.txt`, put the token in `token.txt`, run `python app.py`.

## Demo script
1. Ask: "What items have been found?"
2. Say: "I lost my black Dell backpack in the library" and answer Findy's questions. Match #1 appears with the AI's reason and a line in the graph.
3. Claim found item #1 with "panda keychain on the zipper" -> the reunited counter goes up.

## Tech
Python standard library + `huggingface_hub` (InferenceClient), model `Qwen/Qwen2.5-72B-Instruct` (change with the `MODEL_ID` setting), SQLite, a tiny built-in web server.

## Contributing
See [CONTRIBUTING.md](CONTRIBUTING.md). MIT licensed.
