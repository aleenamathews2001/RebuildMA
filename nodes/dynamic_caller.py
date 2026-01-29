from core.state import MarketingState
from baseagent import get_member_dependency, call_mcp_v2
import logging
from langchain_core.messages import AIMessage
import os
import json
from nodes.completion import get_available_fields
from langgraph.types import Command, interrupt

async def review_proposal_node(state: MarketingState) -> Command:
    """
    Node that handles the human review interruption.
    Routes back to dynamic_caller after approval/edit.
    """
    logging.info("⏸️ [ReviewProposal] Entering review node")
    
    proposal_plan = state.get("pending_proposal_plan")
    proposal_details = state.get("pending_proposal_details")
    
    if not proposal_plan or not proposal_details:
        logging.error("❌ [ReviewProposal] No pending plan/details found in state!")
        return Command(goto="dynamic_caller", update={"error": "Missing proposal data"})

    # ✅ Extract contact/related record info from result_sets (populated by Safe Execution)
    result_sets = state.get("shared_result_sets", {})
    contact_count = 0
    related_records = []
    
    # Try to find contact list (prioritize 'contacts' key if exists)
    if "contacts" in result_sets:
         related_records = result_sets["contacts"]
         contact_count = len(related_records)
    else:
        # Fallback: Look for any list that looks like records
        for key, val in result_sets.items():
            if isinstance(val, list) and len(val) > 0 and key != "previous_result":
                 # Heuristic: Check if items have Email/Name or look like contacts
                 first = val[0]
                 if isinstance(first, dict) and ("Email" in first or "Name" in first or "Id" in first):
                      related_records = val
                      contact_count = len(val)
                      break
    
    # Construct review message
    object_name = proposal_details.get("object_name")
    action_type = proposal_details.get("action_type")
    
    # Enrich with schema label logic if possible (simplified from previous version)
    available_fields = []
    if object_name:
        try:
            available_fields = get_available_fields(object_name)
        except Exception as e:
            logging.warning(f"⚠️ Failed to load schema for {object_name}: {e}")

    def get_label(field_name):
        meta = next((f for f in available_fields if f["name"].lower() == field_name.lower()), None)
        return meta["label"] if meta else field_name

    review_msg = {
        "type": "review_proposal",
        "proposal": {
            "object": object_name,
            "fields": [{"name": k, "value": v, "label": get_label(k)} for k,v in proposal_details.get("fields", {}).items()],
            "action_type": action_type,
            "contact_count": contact_count,
            "related_records": related_records,
            "available_fields": available_fields
        },
        "message": f"I plan to {action_type} a {object_name}. Found {contact_count} related records."
    }

    # 🛑 TRIGGER INTERRUPT
    logging.info(f"⏸️ [ReviewProposal] Triggering INTERRUPT: {json.dumps(review_msg)[:100]}...")
    user_feedback = interrupt(json.dumps(review_msg))
    
    logging.info(f"▶️ [ReviewProposal] RESUMED with feedback: {user_feedback}")

    # Apply Edits
    import re
    if "Details:" in user_feedback:
        try:
            details_part = user_feedback.split("Details:", 1)[1].split(".")[0]
            matches = re.findall(r"(\w+)='([^']*)'", details_part)
            
            if matches and proposal_plan.get("calls"):
                updates_map = dict(matches)
                logging.info(f"✏️ [ReviewProposal] Applying edits: {updates_map}")
                
                for call in proposal_plan["calls"]:
                     tool_name = call.get("tool", "").lower()
                     if any(x in tool_name for x in ["upsert", "create", "update"]):
                         args = call.get("arguments", {})
                         call_obj = args.get("object_name") or args.get("object")
                         # Safety check: object match
                         if call_obj != object_name:
                             continue
                             
                         if "records" in args and isinstance(args["records"], list) and args["records"]:
                             rec = args["records"][0]
                             if "fields" not in rec: rec["fields"] = {}
                             for k, v in updates_map.items():
                                 rec["fields"][k] = v
                         elif "fields" in args:
                             for k, v in updates_map.items():
                                 args["fields"][k] = v
        except Exception as e:
             logging.error(f"❌ [ReviewProposal] Failed to parse edits: {e}")

    # Return to dynamic_caller with override
    return Command(
        goto="dynamic_caller",
        update={
            "plan_override": proposal_plan,
            "pending_proposal_plan": None, 
            "pending_proposal_details": None
        }
    )

async def dynamic_caller(state: MarketingState) -> Command:
    """
    Generic MCP caller - invokes any MCP based on orchestrator's decision.
    """
    service_name = state.get("next_action")
    if not service_name or service_name == "complete":
        return Command(goto="completion", update={"next_action": "complete"})

    logging.info(f"🛰 [DynamicCaller] Invoking {service_name}")
    
    parent_member = state.get("parent_member", "Marketing Agent")
    registry = get_member_dependency(parent_member=parent_member)
    config = registry.get(service_name)
    
    if not config:
        logging.warning(f"Service {service_name} not found")
        return Command(goto="orchestrator", update={"error": f"Service {service_name} not found", "next_action": "complete"})

    # Track usage
    called = state.get("called_services", [])
    if service_name not in called:
        called.append(service_name)

    mcp_result = None
    
    # 1. CHECK FOR PLAN OVERRIDE (Resume Execution)
    if state.get("plan_override"):
        logging.info(f"🔄 [DynamicCaller] Executing PLAN OVERRIDE for {service_name}")
        try:
            mcp_result = await call_mcp_v2(service_name, config, state)
            # Clear override after use
            # We do this by returning update at the end
        except Exception as e:
            logging.exception(f"❌ Error executing override: {e}")
            return Command(goto="orchestrator", update={"error": str(e), "plan_override": None})
            
    else:
        # 2. STANDARD EXECUTION (Generate Plan)
        try:
            mcp_result = await call_mcp_v2(service_name, config, state)
        except Exception as e:
            logging.exception(f"❌ Error calling MCP {service_name}: {e}")
            return Command(goto="orchestrator", update={"error": str(e)})

    # 🛑 3. CHECK FOR PROPOSAL STATUS
    if mcp_result and mcp_result.get("status") == "proposal":
        proposal = mcp_result.get("proposal", {})
        generated_plan = mcp_result.get("generated_plan", {})
        
        # ✅ CAPTURE INTERMEDIATE RESULTS (contacts, etc.)
        # If we executed safe tools, their results are here. We MUST persist them.
        partial_results = mcp_result.get("result_sets", {})
        
        logging.info(f"🛑 [DynamicCaller] Proposal generated. Handing off to ReviewProposal node.")
        
        return Command(
            goto="review_proposal",
            update={
                "pending_proposal_plan": generated_plan,
                "pending_proposal_details": proposal,
                "called_services": called, 
                "shared_result_sets": partial_results 
            }
        )

    # 4. STANDARD COMPLETION (Success or Error)
    # Store results
    results = state.get("mcp_results") or {}
    results[service_name] = mcp_result
    
    # Shared results handling with CONTEXT REFRESH support
    # We now manually merge to allow replacing stale keys (requires state.py reducer change to Replace)
    current_shared = state.get("shared_result_sets") or {}
    shared_update = current_shared.copy() # Start with current state
    
    if mcp_result and mcp_result.get("result_sets"):
        new_sets = mcp_result["result_sets"]
        
        # INTELLIGENT CONTEXT REFRESH
        # If we are getting a new primary object (Campaign, Contact), we should prune the old conflicting one
        # to prevents "merging" a new Campaign ID with an old Campaign ID list (if list modification logic existed)
        # OR essentially just ensure we have the clean new value.
        
        for key, val in new_sets.items():
            if key != "previous_result":
                # Direct overwrite (Standard dict behavior)
                shared_update[key] = val
                
    # Summary for history
    execution_summary = mcp_result.get('execution_summary', {}) if mcp_result else {}
    summary_text = f"Executed {service_name}. Result: {json.dumps(execution_summary)}"
    
    return Command(
        goto="orchestrator",
        update={
            "mcp_results": results,
            "shared_result_sets": shared_update, # Pass FULL updated dict (Requires Replace Reducer)
            "current_agent": service_name,
            "messages": [AIMessage(content=summary_text)],
            "called_services": called,
            "plan_override": None, # Ensure cleared
            "pending_proposal_plan": None # Ensure cleared
        }
    )