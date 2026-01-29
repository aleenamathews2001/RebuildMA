# core/state.py
from typing import TypedDict, List, Dict, Any, Optional, Annotated
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
import operator


def merge_dicts(left: Optional[Dict], right: Optional[Dict]) -> Optional[Dict]:
    """
    Reducer that merges dicts, preserving left if right is None/empty.
    This ensures salesforce_data persists across state updates.
    """
     
    if right is None or (isinstance(right, dict) and len(right) == 0):
        return left
    if left is None:
        return right
    # Merge: right takes precedence for overlapping keys
    result = {**left, **right}
    return result



def merge_history(left: Optional[List[Dict]], right: Optional[List[Dict]]) -> Optional[List[Dict]]:
    """
    Reducer that appends new history items to the existing list.
    """
    if left is None:
        return right
    if right is None:
        return left
    return left + right

class MarketingState(TypedDict):
    user_goal: str
    messages: Annotated[List[BaseMessage], add_messages]
    
    # Orchestrator routing
    iteration_count: int
    max_iterations: int
    next_action: str  # "salesforce" | "brevo" | "linkly" | "complete"
    current_agent: str
    
    # Data coming back from sub-agents - use merge_dicts to preserve across updates
    # Data coming back from sub-agents - use merge_dicts to preserve across updates
    salesforce_data: Optional[Dict[str, Any]]
    brevo_results: Optional[Dict[str, Any]]
    linkly_links: Optional[Dict[str, Any]]
    
    # Generic MCP results storage for dynamic handling
    # 🔴 Removed merge_dicts to allow clearing (overwrite with None) via server.py
    mcp_results: Optional[Dict[str, Any]]
    
    # Persistent Session History
    session_history: Annotated[Optional[List[Dict[str, Any]]], merge_history]
    
    # 🔑 Session Context: Stores created records across multiple requests in same WebSocket session
    session_context: Annotated[Optional[Dict[str, Any]], merge_dicts]
      
    # ✅ SHARED RESULT SETS: Data persistence across agents (e.g. campaign data for Brevo)
    # Changed from merge_dicts to Replace reducer to allow full control (and key deletion) by nodes
    shared_result_sets: Annotated[Optional[Dict[str, Any]], lambda x, y: y if y is not None else x]
    
    # ✅ TASK DIRECTIVES: For multi-step workflows (e.g. update CampaignMember status after email send)
    task_directive: Optional[str]
    pending_updates: Optional[Dict[str, Any]]

    # 🔗 CREATED RECORDS: For LWC hyperlink generation (extracted by completion node)
    created_records: Optional[Dict[str, Any]]

    # ✅ EMAIL WORKFLOW CONTEXT: Temporary state for deterministic email workflow
    email_workflow_context: Optional[Dict[str, Any]]

    engagement_workflow_context: Optional[Dict[str, Any]]

    # Final result + errors
    error: Optional[str]
    final_response: Optional[str]
    
    # ✅ FAIL FLAG (To stop loops)
    workflow_failed: Optional[bool]
    
    # ✅ EMAIL BUILDER CONTENT
    # Removed merge_dicts to allow explicit clearing (setting to None)
    generated_email_content: Optional[Dict[str, Any]]
    
    # ✅ SAVE TEMPLATE WORKFLOW CONTEXT
    save_workflow_context: Optional[Dict[str, Any]]
    
    # 🔄 ACTIVE WORKFLOW (For Sticky Routing)
    # If set, bypasses orchestrator and goes directly to this agent/node
    active_workflow: Optional[str]

    # ✅ REVIEW PROPOSAL STATE
    # Used to resume execution after interrupt
    plan_override: Optional[Dict[str, Any]]
    pending_proposal_plan: Optional[Dict[str, Any]]
    pending_proposal_details: Optional[Dict[str, Any]]


class EmailAgentState(TypedDict):
    """
    State optimized for the Email Builder Agent.
    Sub-set or compatible with MarketingState for easy handoff.
    """
    user_goal: str
    messages: Annotated[List[BaseMessage], add_messages]
    
    # Context needed for drafting
    session_context: Optional[Dict[str, Any]]
    
    # Output
    generated_email_content: Dict[str, Any]
    final_response: Optional[str]
    error: Optional[str]
    
    # 🔄 ACTIVE WORKFLOW (For Sticky Routing)
    active_workflow: Optional[str]
    
    # Routing
    next_action: Optional[str]



