---
name: explain-screen
description: Explain what is on the user's screen (an error, a dialog, code) and what to do next. Use when the user says "what is this", "explain this", "what does this error mean" with a screenshot attached.
---

1. Read the attached screenshot first. If there is none, call mcp__desktop__screenshot.
2. Say in one or two spoken sentences what it is and the single most likely cause.
3. Give the fix as concrete steps. Code or commands go in fenced blocks (shown, not spoken).
4. If the text is too small to read, say so and ask the user to zoom in rather than guessing.
5. If it is the remote laptop (remote mode), never assume you can run anything: hand over
   code with show_code for the user to paste.
