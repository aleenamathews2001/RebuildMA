from baseagent import get_member_dependency, fetch_prompt_metadata, resolve_placeholders, call_llm
from langchain_core.messages import AIMessage
import logging
from core.state import MarketingState


async def orchestrator_node(state: MarketingState) -> MarketingState:
    """
    Simplified orchestrator - all logic is now in Marketing Agent Orchestrator prompt template.
    This node just:
    3. Calls LLM
    4. Routes based on response
    """
    logging.info("🎯 Orchestrator analyzing workflow...")
     
    # Iteration guard
    state["iteration_count"] = state.get("iteration_count", 0) + 1
    if state["iteration_count"] >= state.get("max_iterations", 15):
        logging.warning("Max iterations reached, completing workflow")
        state["next_action"] = "complete"
        state["error"] = "Maximum iterations reached"
        return state

    # Get registry for services_info
    parent_member = state.get("parent_member", "Marketing Agent")
    registry = get_member_dependency(parent_member=parent_member)
     
    # Build services_info string
    services_info = "\n".join(
        f"- {name}: {meta.get('description', 'No description')}"
        for name, meta in registry.items()
    )
    
    # Build progress summary
    progress_summary = _build_progress_summary(state)
    
    logging.info(f"🔍 [Orchestrator] Progress Summary:\n{progress_summary}")

    # Store dynamic values in state for placeholder resolution
    state["services_info"] = services_info
    state["progress_summary"] = progress_summary
    
    # Valid actions for validation (casual_chat will be handled separately)
    logging.info(f"Valid actions: {list(registry.keys())}")
    valid_actions = list(registry.keys()) + ["complete","EngagementWorkflow", "Email Builder Agent", "EmailBuilderAgent"]
    state["valid_actions"] = valid_actions
    
    # Fetch prompt from Salesforce
    prompt_meta = fetch_prompt_metadata("Marketing Agent Prompt")
    if not prompt_meta:
        logging.error("❌ Marketing Agent Orchestrator Prompt not found in Salesforce!")
        state["next_action"] = "complete"
        state["error"] = "Orchestrator prompt template not found"
        return state

    # Resolve placeholders
    resolved_prompt = resolve_placeholders(
        prompt=prompt_meta["prompt"],
        configs=prompt_meta["configs"],
        state=state
    )
    
    # Build user prompt
    user_prompt = f"""User Goal: {state['user_goal']}

Progress So Far:
{progress_summary}

Based on the User Goal and Progress Summary above:
- If the goal is ALREADY realized by the completed operations, respond with 'complete'
- If there is NEW work to be done, choose the next agent
- **PRIORITY**: If the user asks to "track engagement", "check clicks", "find interested members", or "analyze links", you MUST route to 'EngagementWorkflow'.
- Do NOT repeat successful operations

What should we do next? Respond with ONLY one of: Salesforce MCP, Brevo MCP, Linkly MCP , EngagementWorkflow, Email Builder Agent, complete, casual_chat:{{message}}"""

    try:
        # Call LLM
        raw_response = await call_llm(
            system_prompt=resolved_prompt,
            user_prompt=user_prompt,
            default_model=prompt_meta["model"],
            default_provider=prompt_meta["provider"],
            default_temperature=0.0,
        )
        
        normalized = raw_response.strip()
        logging.info(f"Orchestrator decision (raw): {raw_response}")
        logging.info(f"Orchestrator decision (normalized): {normalized}")

        # Handle casual chat response
        if normalized.startswith("casual_chat:"):
            logging.info("💬 Casual chat detected - generating witty response")
            user_message = normalized.replace("casual_chat:", "").strip()
            
            # Generate witty, contextual response using LLM
            casual_prompt = f"""The user said: "{user_message}"

Generate a fun, witty, clever response that:
1. Directly replies to their message in a playful way
2. Briefly mentions you're a Marketing Agent (1-2 sentences max)
3. Hints at your capabilities (Salesforce, Brevo, Linkly)

Keep it conversational, friendly, and engaging. No formal lists or bullet points."""

            try:
                witty_response = await call_llm(
                    system_prompt="You are a friendly, witty Marketing Agent assistant.",
                    user_prompt=casual_prompt,
                    default_model=prompt_meta["model"],
                    default_provider=prompt_meta["provider"],
                    default_temperature=0.7,  # Higher temperature for creativity
                )
                
                state["next_action"] = "complete"
                state["final_response"] = witty_response.strip()
                return state
                
            except Exception as e:
                logging.error(f"Failed to generate casual response: {e}")
                # Fallback to simple response
                state["next_action"] = "complete"
                state["final_response"] = f"Hey there! 👋 I'm your Marketing Agent, ready to help with Salesforce campaigns, Brevo emails, and Linkly tracking links. What can I do for you today?"
                return state

        # Validate response
        if normalized not in valid_actions:
            logging.warning(f"Invalid routing decision: {raw_response}, defaulting to complete")
            normalized = "complete"

        state["next_action"] = normalized
        state["current_agent"] = "orchestrator"

        # state.setdefault("messages", [])
        # state["messages"].append(
        #     AIMessage(content=f"Orchestrator decision: Route to {normalized}")
        # )

        logging.info(f"✅ Routing decision: {normalized}")

    except Exception as e:
        logging.error(f"Orchestrator error: {e}", exc_info=True)
        state["error"] = f"Orchestrator failed: {str(e)}"
        state["next_action"] = "complete"

    return state


def _build_progress_summary(state: MarketingState) -> str:
    """
    Build a DETAILED, DYNAMIC summary of all MCP executions.
    """
    summary_parts = []
    
    # Show pending work FIRST
    task_directive = state.get("task_directive")
    pending_updates = state.get("pending_updates")
    
    if task_directive or pending_updates:
        pending_section = "⚠️  PENDING WORK:\n"
        
        if task_directive:
            pending_section += f"  🎯 Directive: {task_directive}\n"
        
        if pending_updates:
            operation = pending_updates.get("operation", "unknown")
            reason = pending_updates.get("reason", "")
            pending_section += f"  📌 Operation: {operation}\n"
            if reason:
                pending_section += f"  📝 Reason: {reason}\n"
        
        summary_parts.append(pending_section)
    
    # Check for Generated Email Content
    generated_email = state.get("generated_email_content")
    if generated_email:
        subject = generated_email.get("subject", "No subject")
        summary_parts.append(f"✅ EMAIL CONTENT GENERATED:\n  Subject: {subject}\n  (Content available in state)")

    # MCP results summary
    mcp_results = state.get("mcp_results", {})
    if not mcp_results:
        if summary_parts:
            return "\n\n".join(summary_parts)
        return "ℹ️ No MCPs have been called yet."
    
    for service_name, data in mcp_results.items():
        if not data:
            continue
            
        exec_summary = data.get("execution_summary", {})
        tool_results = data.get("tool_results", [])
        
        if exec_summary:
            total_calls = exec_summary.get("total_calls", 0)
            successful = exec_summary.get("successful_calls", 0)
            failed = exec_summary.get("failed_calls", 0)
            
            # Extract operations details
            operations_detail = []
            for result in tool_results[-10:]:  # Last 10 operations
                tool_name = result.get("tool_name", "unknown")
                status = result.get("status", "unknown")
                
                # Try to extract summary from response
                response_obj = result.get("response")
                tool_output_text = ""
                
                if response_obj and hasattr(response_obj, 'content'):
                    try:
                        texts = []
                        for item in response_obj.content:
                            if hasattr(item, 'text'):
                                texts.append(item.text)
                        if texts:
                            tool_output_text = " | ".join(texts)
                            if len(tool_output_text) > 1000:
                                tool_output_text = tool_output_text[:997] + "..."
                    except Exception:
                        pass
                
                if tool_output_text:
                    op_desc = f"{tool_name} -> {tool_output_text}"
                else:
                    # Fallback: show request arguments
                    request = result.get("request", {})
                    details = []
                    for k, v in request.items():
                        if isinstance(v, dict):
                            flat_v = ", ".join(f"{sub_k}={sub_v}" for sub_k, sub_v in v.items())
                            details.append(f"{k}: {{{flat_v}}}")
                        else:
                            details.append(f"{k}={v}")
                    
                    args_str = ", ".join(details)
                    op_desc = f"{tool_name} ({args_str})"

                operations_detail.append(f"{op_desc} ({status})")
            
            ops_str = "\n  - ".join(operations_detail) if operations_detail else "No specific operations"
            
            summary_parts.append(
                f"✅ {service_name.upper()} COMPLETED:\n"
                f"  Stats: {total_calls} calls ({successful} success, {failed} failed)\n"
                f"  Operations:\n  - {ops_str}"
            )
        else:
            summary_parts.append(f"⚠️ {service_name}: Called but no detailed summary available")

    if not summary_parts:
        return "ℹ️ No operations recorded yet."
    
    return "\n\n".join(summary_parts)
