# RNAHarness gateway (P0)

```
channel (HTTP today, BioClaw adapters later)
   │  handleInbound(chat_jid, content)
   ▼
gateway  session mapping · ack/progress · artifacts
   │  Kernel.run(prompt) -> events
   ▼
OpenHarness  `oh -p ... --output-format stream-json`
   │  MCP stdio
   ▼
lab_mcp  lab_run / lab_status / lab_files + 6 typed RNA tools
```

One loop only: the gateway does not plan and does not call research tools.

## Run

```bash
cd gateway
npm install
npm run build && npm start
# or without a build step (Node >= 22.6):
npm run dev
```

Environment:

| var | default | meaning |
|---|---|---|
| `GATEWAY_PORT` | `8787` | HTTP port; expose only this one |
| `GATEWAY_HOST` | `127.0.0.1` | bind address |
| `GATEWAY_DATA_DIR` | `gateway-data` | session map + per-chat workspaces |
| `OPENHARNESS_CMD` | `oh` | kernel executable |
| `KERNEL_TIMEOUT_MS` | `600000` | hard kill for one kernel turn |

## API

```text
POST /messages                  {"chat_jid":"http:u1","content":"折叠 ACGUACGU"}
                                -> 202 {"task_id":"..."}
GET  /tasks/<id>/events         SSE: task.created, task.ack, tool.started,
                                tool.completed, answer.delta,
                                answer.completed | task.failed
GET  /tasks/<id>                buffered events as JSON
GET  /messages?chat_jid=...     outbound messages for a chat
GET  /health
```

## Kernel wiring

`oh` must have the `lab_mcp` server configured. Merge
`../config/openharness.settings.example.json`'s `mcp_servers` block into
`~/.openharness/settings.json`, then check it:

```bash
oh mcp list
oh -p "调用 lab_run 用 vienna 折叠 ACGUACGU" --output-format stream-json
```

Tool names arrive as `mcp__lab_mcp__lab_run` (OpenHarness prefixes
`mcp__<server>__<tool>`).

## Tests

```bash
node --experimental-strip-types --test test/*.test.ts
```

The tests use a fake kernel, so they run without OpenHarness or an API key.
