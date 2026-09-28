"""A DeepAgents research agent that plans, delegates and writes its report to a file.

The agent plans with ``write_todos``, delegates to a ``librarian`` subagent through ``task``, looks a term
up in a local knowledge base, and writes, revises and reads files in the virtual filesystem.

    uv run python examples/deepagents/research_agent.py            # scripted model, no API key needed
    uv run python examples/deepagents/research_agent.py --live "your question"

The model is scripted by default. That is not a shortcut around an API key: a run you can reproduce says
more about the agent than a live one, and a live model would make each run differ for reasons that have
nothing to do with how the agent is put together. ``--live`` uses OpenAI and needs ``OPENAI_API_KEY``.

``TodoListMiddleware`` is passed explicitly because it is *not* part of the default deep agent stack --
it comes from ``langchain``, and a stock ``create_deep_agent`` has neither ``write_todos`` nor the
``todos`` state key.

Prints the agent's answer and a short summary of what the run produced, and exports an EQTY
lineage manifest of the run to ``manifests/research_agent.json``.
"""

import argparse
import os
from pathlib import Path
from typing import Any, Dict, List

from deepagents import create_deep_agent
from eqty_lineage.deepagents import EqtyDeepAgentsHandler, eqty_tool
from eqty_sdk import Context, Signer, init, set_active_signer
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

# Written relative to the working directory, next to the ``.eqty_sdk`` store ``init()`` creates.
MANIFEST_PATH = Path("manifests/research_agent.json")

SYSTEM_PROMPT = (
    "You are a research assistant. Plan with write_todos, delegate lookups to the librarian subagent, "
    "keep your notes and drafts in files, and finish with a short report in /report.md."
)

LIBRARIAN = {
    "name": "librarian",
    "description": "Looks up a topic and writes what it found to a file.",
    "system_prompt": "You look things up and write concise notes to /notes.md. Do not editorialise.",
}

KNOWLEDGE_BASE = {
    "cid": (
        "A CID (content identifier) is a self-describing hash that names content rather than a location, "
        "so the same bytes have the same name wherever they are stored."
    ),
    "lineage": (
        "Data lineage links inputs, computations and outputs, so a result can be traced back to the data "
        "and the steps that produced it."
    ),
}


@tool
@eqty_tool
def search_knowledge_base(query: str) -> str:
    """Search the local knowledge base for entries matching the query."""
    words = {w.strip("?.,!").lower() for w in query.split()}
    matches = {key: text for key, text in KNOWLEDGE_BASE.items() if key in words}
    if not matches:
        return f"No entries for '{query}'. Available: {', '.join(KNOWLEDGE_BASE)}."
    return "\n\n".join(f"[{key}] {text}" for key, text in matches.items())


NOTES = (
    "A CID is a self-describing hash: it names content rather than a location, so the same bytes have\n"
    "the same name wherever they are stored.\n"
)
DRAFT = "# CIDs\n\nA CID names content.\n"
FINAL = "# CIDs\n\nA CID names content rather than a location, which is what makes lineage verifiable.\n"


def _call(name: str, call_id: str, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "id": call_id, "args": args}])


SCRIPT: List[AIMessage] = [
    _call(
        "write_todos",
        "p1",
        todos=[
            {"content": "look up what a CID is", "status": "in_progress"},
            {"content": "draft the report", "status": "pending"},
        ],
    ),
    _call("task", "p2", description="Look up what a CID is.", subagent_type="librarian"),
    # the librarian's own turns
    _call("search_knowledge_base", "s1", query="cid"),
    _call("write_file", "s2", file_path="/notes.md", content=NOTES),
    AIMessage(content="Notes are in /notes.md."),
    # back in the research agent
    _call(
        "write_todos",
        "p3",
        todos=[
            {"content": "look up what a CID is", "status": "completed"},
            {"content": "draft the report", "status": "in_progress"},
        ],
    ),
    _call("read_file", "p4", file_path="/notes.md"),
    _call("write_file", "p5", file_path="/report.md", content=DRAFT),
    _call(
        "edit_file",
        "p6",
        file_path="/report.md",
        old_string="A CID names content.",
        new_string="A CID names content rather than a location, which is what makes lineage verifiable.",
    ),
    _call(
        "write_todos",
        "p7",
        todos=[
            {"content": "look up what a CID is", "status": "completed"},
            {"content": "draft the report", "status": "completed"},
        ],
    ),
    AIMessage(content="The report is in /report.md."),
]


class ScriptedModel(GenericFakeChatModel):
    """Replays the script above, and accepts the agent's tool belt without binding it."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedModel":
        return self


def build_agent(live: bool) -> Any:
    if live:
        from langchain_openai import ChatOpenAI

        model: Any = ChatOpenAI(model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"), max_tokens=2048)
    else:
        model = ScriptedModel(messages=iter(SCRIPT))

    agent = create_deep_agent(
        model=model,
        tools=[search_knowledge_base],
        system_prompt=SYSTEM_PROMPT,
        subagents=[LIBRARIAN],
        middleware=[TodoListMiddleware()],
        name="research-agent",
    )

    # DeepAgents' built-in belt (write_file, edit_file, task, ...) is built by middleware and is
    # not ours to decorate, so content-address the live tool objects the compiled graph assembled.
    # A graph without a "tools" node degrades to the name/description stub rather than raising.
    tools_node = getattr(agent.nodes.get("tools"), "bound", None)
    for tool_obj in (getattr(tools_node, "tools_by_name", None) or {}).values():
        eqty_tool(tool_obj)

    return agent


def summarize(result: Dict[str, Any]) -> str:
    files = sorted(result.get("files") or {})
    todos = result.get("todos") or []
    done = sum(1 for todo in todos if todo.get("status") == "completed")

    lines = [
        f"files:            {len(files)} ({', '.join(files) or 'none'})",
        f"plan items:       {len(todos)} ({done} completed)",
        f"messages:         {len(result.get('messages') or [])}",
    ]
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the deep research agent.")
    parser.add_argument("question", nargs="?", default="What is a CID? Write me a short report.")
    parser.add_argument("--live", action="store_true", help="use OpenAI instead of the scripted model")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # init() is process-global: once at process startup, never per run. A second call is a
    # silent no-op that leaves the first store in place.
    cfg = init(default_context=Context.new("Deep Research")).set_store_all_blobs(True)
    set_active_signer(Signer.load_or_create(name="deep_research"))

    agent = build_agent(args.live)

    result = agent.invoke(
        {"messages": [HumanMessage(args.question)]},
        # One handler per invocation, built here at the call site; merged into the existing config.
        config={"recursion_limit": 80, "callbacks": [EqtyDeepAgentsHandler()]},
    )

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    cfg.get_default_context().export(MANIFEST_PATH)

    print(result["messages"][-1].content)
    print()
    print(summarize(result))
    print(f"manifest:         {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
