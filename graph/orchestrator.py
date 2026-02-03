from typing import TypedDict, Annotated, Literal, Optional
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.base import BaseCheckpointSaver
from core.state import MarketingState
from graph.marketing_agent import build_marketing_graph
import logging

# Define Orchestrator State (can be same as MarketingState or a superset)
class OrchestratorState(MarketingState):
    selected_agent: Optional[str]

# 1. Orchestrator Node (The Boss)
async def marketing_orchestrator(state: OrchestratorState) -> OrchestratorState:
    """
    Decides which agent to call. ->Currently hardcoded to 'marketing_agent'.
    """
    logging.info("🧠 [Orchestrator] Analyzing request...")
    # Hardcoded routing
    decision = "marketing_agent"
    logging.info(f"👉 [Orchestrator] Routing to: {decision}")
    state["selected_agent"] = decision
    return state

# 3. Router logic
def route_orchestrator(state: OrchestratorState) -> str:
    agent = state.get("selected_agent")
    if agent == "marketing_agent":
        return "marketing_agent"
    return END

# Build the Graph
def build_orchestrator_graph(checkpointer: BaseCheckpointSaver = None):
    builder = StateGraph(OrchestratorState)
    marketing_graph = build_marketing_graph() 
    builder.add_node("marketing_orchestrator", marketing_orchestrator)
    # ADD THE COMPILED GRAPH AS A NODE (Glass Box)
    builder.add_node("marketing_agent", marketing_graph)
    builder.set_entry_point("marketing_orchestrator")
    builder.add_conditional_edges(
        "marketing_orchestrator",
        route_orchestrator,
        {
            "marketing_agent": "marketing_agent",
            END: END
        }
    )
    builder.add_edge("marketing_agent", END)
    return builder.compile(checkpointer=checkpointer)
