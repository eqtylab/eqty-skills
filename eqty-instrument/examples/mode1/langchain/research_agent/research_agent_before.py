"""A full-featured LangGraph research agent.

The graph is a tool-calling research agent:

    START -> agent -> (tool calls?) -> tools -> agent -> ... -> summarize -> END

Requires OPENAI_API_KEY.

    uv run python examples/langchain/research_agent.py "your question"
"""

import argparse
import os
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

KNOWLEDGE_BASE = {
    "cid": (
        "A CID (content identifier) is a self-describing hash that uniquely and "
        "immutably identifies a piece of content. CIDv1 encodes a multibase prefix, "
        "a multicodec content type, and a multihash digest."
    ),
    "sha-256": (
        "SHA-256 is a cryptographic hash function producing a 256-bit (32-byte) "
        "digest. It is the default multihash used for CIDs."
    ),
    "lineage": (
        "Data lineage links inputs, computations, and outputs, so a result can be "
        "traced back to the data and the steps that produced it."
    ),
}

SYSTEM_PROMPT = (
    "You are a research assistant. Use the search_knowledge_base tool to look up "
    "facts and the calculate tool for any arithmetic. Cite which knowledge base "
    "entries you used. Answer concisely."
)


@tool
def search_knowledge_base(query: str) -> str:
    """Search the local knowledge base for entries matching the query."""
    query_words = {w.strip("?.,!").lower() for w in query.split()}
    matches = {
        key: text
        for key, text in KNOWLEDGE_BASE.items()
        if key in query_words or any(w in text.lower() for w in query_words)
    }
    if not matches:
        return f"No entries found for '{query}'. Available entries: {', '.join(KNOWLEDGE_BASE)}."
    return "\n\n".join(f"[{key}] {text}" for key, text in matches.items())


@tool
def calculate(expression: str) -> str:
    """Evaluate a basic arithmetic expression, e.g. '3 * 32'."""
    allowed = set("0123456789+-*/(). ")
    if not expression or not set(expression) <= allowed:
        return "Error: only digits and + - * / ( ) are supported."
    try:
        return str(eval(expression, {"__builtins__": {}}, {}))
    except Exception as exc:  # noqa: BLE001
        return f"Error: {exc}"


TOOLS = [search_knowledge_base, calculate]


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    question: str
    answer: str


def build_graph(model=None):
    if model is None:
        model = ChatOpenAI(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            max_tokens=2048,
        ).bind_tools(TOOLS)

    def agent(state: AgentState) -> dict:
        messages = [SystemMessage(SYSTEM_PROMPT), *state["messages"]]
        return {"messages": [model.invoke(messages)]}

    def route_after_agent(state: AgentState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return "summarize"

    def summarize(state: AgentState) -> dict:
        """Deterministic (non-LLM) node: extract the final answer from the transcript."""
        last = state["messages"][-1]
        text = last.content if isinstance(last.content, str) else str(last.content)
        return {"answer": text}

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(TOOLS))
    graph.add_node("summarize", summarize)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route_after_agent, ["tools", "summarize"])
    graph.add_edge("tools", "agent")
    graph.add_edge("summarize", END)
    return graph.compile()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the research agent.")
    parser.add_argument(
        "question",
        nargs="?",
        default=(
            "What is a CID, and how many bytes do 3 SHA-256 digests take in total? "
            "Use the knowledge base and calculator."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = build_graph()
    result = app.invoke(
        {"messages": [HumanMessage(args.question)], "question": args.question, "answer": ""},
        config={"recursion_limit": 25},
    )
    print(result["answer"])


if __name__ == "__main__":
    main()
