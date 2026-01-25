import logging
import json
from baseagent import call_llm
from core.state import EmailAgentState
from baseagent import call_llm
from core.state import EmailAgentState

async def email_builder_node(state: EmailAgentState) -> EmailAgentState:
    """
    Generates email content based on user goal using an LLM.
    """
    logging.info("📧 [EmailBuilderAgent] Generating email content...")
    
    # 1. Check for immediate exit intent
    messages = state.get("messages", [])
    user_goal = state.get("user_goal", "")
    
    logging.info(f"   🔍 DEBUG: messages count={len(messages)}, user_goal='{user_goal}'")
    
    last_msg = ""
    if messages:
        # Check if message is object or string to be safe
        obj = messages[-1]
        if hasattr(obj, 'content'):
            last_msg = obj.content
        elif isinstance(obj, dict): 
            last_msg = obj.get('content', '')
        else:
            last_msg = str(obj)
    elif user_goal:
        # Fallback to user_goal if messages is empty (which it is in server.py initial_state)
        last_msg = user_goal
        
    last_msg = last_msg.lower()
    logging.info(f"   🔍 DEBUG: final last_msg for intent check='{last_msg}'")

    # 🟢 SAVE INTENT
    # LWC sends "save this email template to brevo..."
    # We also check for simple "save" if the user types it manually
    if ("save" in last_msg and "template" in last_msg) or ("save" in last_msg and "brevo" in last_msg):
        logging.info("   💾 User requested to SAVE template.")
        
        email_data = state.get("generated_email_content")
        if not email_data:
            state["final_response"] = "I don't have a draft to save yet. Let's create one first!"
            return state

        # 🟢 ROUTE TO SAVE TEMPLATE WORKFLOW
        logging.info("   🔄 Offloading save logic to SaveTemplateWorkflow.")
        
        # Determine current campaign ID to pass context
        session_context = state.get("session_context", {})
        shared_results = session_context.get("shared_result_sets", {})
        campaigns = shared_results.get("Campaign", [])
        
        # We don't need to do the work here, just ensure context is safer for next workflow if needed
        # But the workflow reads from session_context, so we are good.
        
        state["next_action"] = "save_template" # Triggers route_builder
        state["active_workflow"] = None # Exit sticky mode so we transition out
        
        # We don't set final_response here because the workflow will produce it.
        # But wait, if we yield state, the UI might need an update?
        # The Orchestrator will run next, or the SaveWorkflow?
        # SaveWorkflow runs -> Orchestrator runs -> Final Response to User.
        
        return state

    # 🔴 EXIT INTENT
    exit_keywords = ["stop", "exit", "done", "cancel", "salesforce", "linkly", "brevo", "main menu"]
    if any(w in last_msg for w in exit_keywords):
        logging.info("   🛑 User requested exit from Email Builder.")
        state["active_workflow"] = None  # Clear stickiness
        state["final_response"] = "Exiting Email Builder. What else can I do for you?"
        return state

    # 2. Otherwise, set sticky flag
    state["active_workflow"] = "email_builder_agent"

    
    user_goal = state.get("user_goal", "")
    session_context = state.get("session_context", {})
    
    # current_content = state.get("generated_email_content") # Copied from below
    current_content = state.get("generated_email_content")
    
    # Extract conversation history for context
    history_text = ""
    if messages:
        # Get last few messages to understand context/revisions
        recent = messages[-5:] 
        for msg in recent:
            role = "User" if msg.type == "human" else "Assistant"
            history_text += f"{role}: {msg.content}\n"
    
    # Enhanced prompt for iteration
    system_prompt = """You are an expert Email Marketing Copywriter.
Your task is to draft OR REFINE a professional, engaging email based on the user's request and conversation history.

CONTEXT:
If a 'Current Draft' is provided, you must REFINE it based on the user's latest feedback (e.g., "make it shorter", "add signature").
If no draft exists, create a new one.

OUTPUT FORMAT:
Return a JSON object with the following keys:
{
    "subject": "The email subject line",
    "body_html": "The email body in HTML format (clean, responsive, no extra css)",
    "body_text": "The plain text version of the email",
    "tone": "The tone used (e.g., Professional, Friendly)",
    "suggested_audience": "Who this email is good for"
}

RULES:
1. ONLY return JSON. No markdown blocking.
2. Be creative but professional.
3. Use placeholders like {{FirstName}} if appropriate for personalization.
4. If the user asks for a revision, keeping the parts they didn't ask to change (unless improvements are needed).
"""

    user_prompt = f"""User Request: {user_goal}

Conversation History:
{history_text}

Context:
{json.dumps(session_context, indent=2)}

Current Draft (if any):
{json.dumps(current_content, indent=2) if current_content else "None"}

Draft/Refine the email now."""

    try:
        # We need a model config - defaulting or getting from somewhere?
        # For this standalone agent, we'll use defaults for now or pass state config if available.
        # Assuming we can use standard call_llm defaults.
        
        response = await call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            default_model="gpt-4o", # Strong model for writing
            default_provider="openai",
            default_temperature=0.7 # Creative
        )
        
        # Parse JSON
        clean_response = response.strip()
        if clean_response.startswith("```json"):
            clean_response = clean_response[7:]
        if clean_response.endswith("```"):
            clean_response = clean_response[:-3]
        
        content = json.loads(clean_response)
        
        state["generated_email_content"] = content
        
        # Generate a conversational response about what was done
        summary_prompt = f"""You just updated/created an email with subject: "{content.get('subject')}".
User's last request was: "{messages[-1].content if messages else user_goal}"

Generate a short, friendly 1-sentence response to the user confirming the action (e.g., "I've added the signature for you." or "Here is the draft email.")."""

        summary_response = await call_llm(
             system_prompt="You are a helpful assistant.",
             user_prompt=summary_prompt,
             default_model="gpt-4o-mini",
             default_provider="openai",
             default_temperature=0.7
        )
        
        state["final_response"] = summary_response.strip()
        logging.info(f"   ✅ Derived subject: {content.get('subject')}")

    except Exception as e:
        logging.error(f"   ❌ Email generation failed: {e}")
        state["error"] = f"Failed to generate email: {str(e)}"
        
    return state
