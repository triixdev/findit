# FindIt - what to say to the judges

**One line:** "The AI connects the person who lost something to the item someone found, shows the link as a graph, and explains why. A fixed rule, not the AI, decides who gets the item."

## The AI's job (their question)
- Node X = the lost complaint. Node Y = the found item.
- The AI reads both and answers: same thing? yes / maybe / no, how sure, and why. That link is the line in the graph.
- It is a reasoning job, not an embedding score: "Both are black Dell backpacks left in the library, so yes (92%)".
- It also runs the chat, calling tools to save reports and start claims.

## What the AI does NOT do
- It never decides ownership and never sees the hidden detail. Ownership is a plain rule (3 tries), because a model can be talked into things.

## Demo (about 90 seconds)
1. Open the app: show the lists and the empty-ish graph.
2. Chat: "I lost my black Dell backpack in the library" -> match #1 + the AI's reason.
3. Point at the graph: lost complaint -> found item line, click it, read the reason.
4. Claim #1 with a wrong detail (fails), then "panda keychain on the zipper" (works). Reunited counter goes up.

## Likely questions
- **Several people lost similar items?** The AI ranks and explains; only the hidden detail proves ownership.
- **Can someone guess the detail?** 3 attempts per item, then in-person at the office. Next: per-person limits and a staff confirm step.
- **Why open source?** Free, anyone can run it, swap the model with one setting. Fits Hacktoberfest.
- **No internet / no token?** Forms and keyword matching still work.
- **Netron?** (only if your mentor really means it) Open the Qwen model file in Netron for one slide: it only displays a model's graph, ours runs on Hugging Face's servers.

## Be upfront about limits
Guessable hidden details, attempts counted per item, no staff step yet. Judges like a clear "what we'd fix next".
