from core.state import MarketingState
from baseagent import get_member_dependency, call_mcp,call_mcp_v2
import logging


async def dynamic_caller(state: MarketingState) -> MarketingState:
    """
    Generic MCP caller - invokes any MCP (Salesforce, Brevo, Linkly) based on orchestrator's decision.
    Calls the MCP's internal tool (e.g., generate_all_toolinput for Salesforce MCP).
    """
    service_name = state.get("next_action")
    if not service_name or service_name == "complete":
        return state

    logging.info(f"🛰 [MCP Caller] Invoking {service_name}")

    parent_member = state.get("parent_member", "Marketing Agent")

    # Get MCP configuration from registry
    registry = get_member_dependency(parent_member=parent_member)
    config = registry.get(service_name)
    
    if not config:
        msg = f"Service {service_name} not found in registry"
        logging.warning(msg)
        state["error"] = msg
        state["next_action"] = "complete"
        return state

    # Track which MCPs were called
    called = state.setdefault("called_services", [])
    if service_name not in called:
        called.append(service_name)

    # Call the MCP (it will invoke its internal tool like generate_all_toolinput)
    try:
        mcp_result = await call_mcp_v2(service_name, config, state)
    except Exception as e:
        logging.exception(f"❌ Error calling MCP {service_name}: {e}")
        state["error"] = f"Error calling MCP {service_name}: {e}"
        return state

    # Store results
    results = state.setdefault("mcp_results", {})
    results[service_name] = mcp_result

    # Persist shared result sets for cross-MCP communication
    if mcp_result and mcp_result.get("result_sets"):
        shared = state.setdefault("shared_result_sets", {})
        for key, val in mcp_result["result_sets"].items():
            if key != "previous_result":  # Don't persist ephemeral results
                shared[key] = val
        logging.info(f"💾 [DynamicCaller] Updated shared_result_sets with keys: {list(mcp_result['result_sets'].keys())}")

    # Set current agent
    state["current_agent"] = service_name
    
    logging.info(f"✅ [MCP Caller] {service_name} completed successfully")
    
    return state