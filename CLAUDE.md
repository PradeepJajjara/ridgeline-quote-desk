# Project Configuration

## Chat History Logging (ALWAYS ACTIVE)

These instructions are permanent and apply to every session in this project.

### Session start
At the start of each session, read `.chat-history/log.md` for previous context. If the file or the `.chat-history/` folder does not exist, create them.

### After every response
After each response, silently append one entry to `.chat-history/log.md` using this EXACT format (including the leading `---` separator):

```
---
- timestamp: "<ISO 8601 timestamp if available, otherwise estimate based on conversation order>"
- user_prompt: "<the user's original prompt>"
- assistant_response_summary: "<summary of what you generated or answered for this prompt>"
- files_affected: "<comma-separated list of files created or modified, or none>"
```

### Rules
- Create the file/folder if they don't exist. **Never delete previous entries** — only append.
- Do all of this **silently** — never ask for confirmation.
- Log **every** prompt/response pair. Never skip an exchange.
- Be precise about `files_affected`: only include files explicitly created or modified during that response. Use `none` if no files changed.
- Keep `assistant_response_summary` concise but specific — mention function names, endpoints, or key decisions made.
- **Never write secrets to the log.** Replace API keys, tokens, passwords, and `.env` values with `***` in every field, including `user_prompt`.
