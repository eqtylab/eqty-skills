"""Serve the travel agent behind a small local browser chat UI.

Requires ``OPENAI_API_KEY`` (and optionally ``OPENAI_MODEL``). Each browser gets a UUID-backed LangGraph
``thread_id``, so a conversation resumes from the checkpointer across turns.

    uv run python examples/langchain/travel_chat_ui.py

The server emits an EQTY lineage manifest for the whole process to
``manifests/travel_chat_ui.json`` (relative to the working directory, alongside the
``.eqty_sdk`` store ``init()`` creates there) when it shuts down.
"""

import argparse
import json
import logging
import os
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import UUID
import webbrowser

from eqty_lineage.langchain import EqtyCallbackHandler
from eqty_sdk import Context, Signer, init, set_active_signer
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from travel_agent import build_graph


logger = logging.getLogger("travel_chat_ui")
# HTTP requests may arrive concurrently; serialize graph invocation so turns of different threads
# cannot interleave inside the shared checkpointer.
invoke_lock = Lock()
MANIFEST_PATH = Path("manifests/travel_chat_ui.json")
# One lineage handler per conversation, keyed like the checkpointer is: reusing a session's
# handler across its turns is what chains turn 1 to turn 3. Read and written only under
# invoke_lock, which is also what keeps two runs from ever sharing one handler in flight.
lineage_handlers: dict[str, EqtyCallbackHandler] = {}


PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Travel Agent</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, sans-serif; background: #101828; color: #e5e7eb; }
    body { margin: 0; } main { max-width: 760px; margin: auto; min-height: 100vh; display: flex; flex-direction: column; }
    header { padding: 24px 16px 12px; display: flex; align-items: start; gap: 12px; justify-content: space-between; } h1 { margin: 0; font-size: 1.5rem; } p { color: #98a2b3; }
    #messages { flex: 1; padding: 8px 16px 100px; } .message { white-space: pre-wrap; padding: 12px; margin: 10px 0; border-radius: 10px; max-width: 85%; }
    .user { background: #155eef; margin-left: auto; } .assistant { background: #27364e; }
    form { position: fixed; bottom: 0; left: 0; right: 0; background: #101828; border-top: 1px solid #344054; padding: 12px; display: flex; gap: 8px; justify-content: center; }
    textarea { width: min(620px, 75vw); resize: vertical; min-height: 42px; padding: 10px; border-radius: 8px; border: 1px solid #475467; background: #182230; color: inherit; }
    button { border: 0; border-radius: 8px; padding: 0 18px; background: #2e90fa; color: white; font-weight: 600; cursor: pointer; } #new-chat { min-height: 36px; background: #344054; white-space: nowrap; } button:disabled { opacity: .55; }
  </style>
</head>
<body><main>
  <header><div><h1>Travel Agent</h1><p>Each browser conversation is a separate LangGraph thread.</p></div><button id="new-chat" type="button">New chat</button></header>
  <section id="messages" aria-live="polite"></section>
</main>
<form id="chat"><textarea id="message" placeholder="Plan a weekend in Lisbon" required></textarea><button>Send</button></form>
<script>
  const sessionKey = "travel-agent-session";
  let sessionId = sessionStorage.getItem(sessionKey) || crypto.randomUUID();
  sessionStorage.setItem(sessionKey, sessionId);
  const messages = document.querySelector("#messages"), form = document.querySelector("#chat"), input = document.querySelector("#message"), button = form.querySelector("button"), newChat = document.querySelector("#new-chat");
  function add(role, text) { const item = document.createElement("article"); item.className = `message ${role}`; item.textContent = text; messages.append(item); item.scrollIntoView({block: "end"}); }
  newChat.addEventListener("click", () => { sessionId = crypto.randomUUID(); sessionStorage.setItem(sessionKey, sessionId); messages.replaceChildren(); input.focus(); });
  form.addEventListener("submit", async event => {
    event.preventDefault(); const message = input.value.trim(); if (!message) return;
    add("user", message); input.value = ""; button.disabled = true;
    try {
      const response = await fetch("/chat", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({session_id: sessionId, message})});
      const payload = await response.json(); if (!response.ok) throw new Error(payload.error || "Request failed");
      add("assistant", payload.answer);
    } catch (error) { add("assistant", `Error: ${error.message}`); }
    finally { button.disabled = false; input.focus(); }
  });
  input.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); form.requestSubmit(); }
  });
</script></body></html>"""


class TravelChatHandler(BaseHTTPRequestHandler):
    app: Any

    def send_json(self, status: HTTPStatus, payload: dict[str, str]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/":
            logger.info("http.not_found method=GET path=%s", self.path)
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        logger.info("ui.page_served client=%s", self.client_address[0])
        body = PAGE.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/chat":
            logger.info("http.not_found method=POST path=%s", self.path)
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        started = time.monotonic()
        session_id = "unknown"
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            session_id = str(UUID(str(payload["session_id"])))
            message = str(payload["message"]).strip()
            if not message:
                raise ValueError("message is required")

            logger.info("chat.started session_id=%s input_chars=%d", session_id, len(message))

            with invoke_lock:
                handler = lineage_handlers.get(session_id)
                if handler is None:
                    handler = lineage_handlers[session_id] = EqtyCallbackHandler()
                result = self.app.invoke(
                    {"messages": [HumanMessage(message)], "question": message, "answer": ""},
                    config={
                        "configurable": {"thread_id": session_id},
                        "run_name": "Travel Assistant",
                        "recursion_limit": 25,
                        "callbacks": [handler],
                    },
                )
            answer = str(result["answer"])
            self.send_json(HTTPStatus.OK, {"answer": answer})
            logger.info(
                "chat.completed session_id=%s output_chars=%d duration_ms=%d",
                session_id,
                len(answer),
                round((time.monotonic() - started) * 1000),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            logger.warning("chat.rejected session_id=%s error=%s", session_id, error)
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        except Exception as error:  # noqa: BLE001
            logger.exception("chat.failed session_id=%s", session_id)
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(error)})

    def log_message(self, format: str, *args: object) -> None:
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve the travel-agent chat UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    for logger_name in ("httpx", "httpcore", "urllib3", "openai", "hyper", "hyper_util"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)
    # init() is process-global and a second call is a silent no-op, so it happens exactly
    # once, here at process startup rather than per chat turn.
    cfg = init(default_context=Context.new("Travel Chat UI")).set_store_all_blobs(True)
    set_active_signer(Signer.load_or_create(name="travel_chat_ui"))
    TravelChatHandler.app = build_graph(checkpointer=MemorySaver())
    server = ThreadingHTTPServer((args.host, args.port), TravelChatHandler)
    url = f"http://{args.host}:{args.port}"
    logger.info("server.started url=%s model=%s", url, os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
    finally:
        server.server_close()
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        cfg.get_default_context().export(MANIFEST_PATH)
        logger.info("lineage.exported path=%s", MANIFEST_PATH)


if __name__ == "__main__":
    main()
