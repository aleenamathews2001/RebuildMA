from core.state import MarketingState
from langgraph.graph import StateGraph, END
from nodes.marketingorchestrator import orchestrator_node
from nodes.dynamic_caller import dynamic_caller
from nodes.completion import completion_node
from workflows.engagement_workflow import build_engagement_workflow
from workflows.email_workflow import build_email_workflow
import logging
 
def route_decision(state: MarketingState) -> str:
    """
    Router for the 'orchestrator' node.

    - If the orchestrator decided we're done (next_action == 'complete'),
      go to 'completion'.
    - If next_action is 'Brevomcp' (or 'EmailWorkflow'), route to specific email workflow.
    - If next_action is 'EngagementWorkflow', route to engagement workflow.
    - Otherwise, go to generic 'dynamic_caller'.
    """
    next_action = state.get("next_action", "complete")
    
    if next_action == "complete":
        return "complete"
    
    # 🚀 INTERCEPT Brevo calls to use the specialized workflow
    if next_action == "Brevo MCP":
        logging.info("🔀 Routing to deterministic Email Workflow")
        return "email_workflow"
        
    if next_action == "EngagementWorkflow":
        logging.info("🔀 Routing to deterministic Engagement Workflow")
        return "engagement_workflow"
        
    return "dynamic_caller"

def build_marketing_graph():
    builder = StateGraph(MarketingState)

    builder.add_node("orchestrator", orchestrator_node)
    builder.add_node("dynamic_caller", dynamic_caller)
    builder.add_node("completion", completion_node)
    
    # ✅ Add Subgraphs
    builder.add_node("email_workflow", build_email_workflow())
    builder.add_node("engagement_workflow", build_engagement_workflow())

    builder.set_entry_point("orchestrator")
    
    builder.add_conditional_edges(
        "orchestrator",
        route_decision,
        {
            "dynamic_caller": "dynamic_caller",
            "email_workflow": "email_workflow",
            "engagement_workflow": "engagement_workflow",
            "complete": "completion",
        },
    )
    
    builder.add_edge("dynamic_caller", "orchestrator")
    builder.add_edge("email_workflow", "orchestrator") # Loop back for next steps
    builder.add_edge("engagement_workflow", "orchestrator") # Loop back for next steps
    builder.add_edge("completion", END)
    
    return builder.compile()
