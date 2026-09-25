def _is_graph_control_flow(error: BaseException) -> bool:
    """Whether LangGraph raised this to move the graph rather than to report a failure.

    ``GraphInterrupt`` (a pause for approval), ``ParentCommand`` and ``GraphDelegate`` all subclass
    ``GraphBubbleUp`` and all reach ``on_chain_error`` looking like a crash. Matched on the base class
    *name*: this package depends on ``langchain-core`` alone and must not import ``langgraph``.
    """
    return any(cls.__name__ == "GraphBubbleUp" for cls in type(error).__mro__)
