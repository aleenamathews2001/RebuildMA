# fastapi_websocket.py
"""
FastAPI WebSocket endpoint for real-time agent communication
"""
import asyncio
import uuid
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from dotenv import load_dotenv
from core.mcp_loader import preload_mcp_tools
from baseagent import get_member_dependency
from graph.marketing_agent import build_marketing_graph
from core.state import MarketingState

import logging

# Load environment variables
load_dotenv()

# ✅ Configure logging to file and console
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("agent.log", mode='a', encoding='utf-8'),
        logging.StreamHandler()
    ],
    force=True
)

app = FastAPI()

 


@app.on_event("startup")
async def startup_event():
    """Pre-load MCP tools on server startup"""
    try:
        logging.info("🚀 Starting Marketing Agent Server...")
        # Get registry which contains configs for all MCPs
        registry = get_member_dependency("Marketing Agent")
        
        # Filter for valid MCP configs that have executionEndpoint
        service_configs = {}
        for name, data in registry.items():
            if data.get("executionEndpoint"):
                # Adapt config format for loader
                config = {
                    "command": "python", # Default command
                    "args": data.get("executionEndpoint"),
                    "env": None
                }
                # Handle JSON string args 
                if isinstance(config["args"], str):
                    try:
                        config["args"] = json.loads(config["args"])
                    except:
                        config["args"] = [config["args"]]
                        
                service_configs[name] = config
        
        if service_configs:
            await preload_mcp_tools(service_configs)
        else:
            logging.warning("⚠️ No valid MCP configurations found to preload")
            
    except Exception as e:
        logging.error(f"❌ Error during startup tool preloading: {e}")

class MessageRequest(BaseModel):
    message: str

class MessageResponse(BaseModel):
    success: bool
    response: str
    iterations: int
    error: str | None = None  # Optional field with default None
    salesforce_data: bool = False


@app.websocket("/ws/chat")
async def run_agent(websocket: WebSocket):
    await websocket.accept()
    
    # 🧠 STATEFUL MEMORY: Persistent checkpointer for this connection
    memory = MemorySaver()
    agent_graph = build_marketing_graph(checkpointer=memory)
    
    # Generate unique session ID for this connection
    session_id = str(uuid.uuid4())
    logging.info(f"🔌 New WebSocket connection. Assigned Session ID: {session_id}")
    thread_config = {"configurable": {"thread_id": session_id}} 


    try:
        while True:
            # 1. RECEIVE message
            data = await websocket.receive_text()
            message_data = json.loads(data)
            user_message = message_data.get("message", "")
            
            # 2. CHECK STATUS (Interrupted vs New)
            # Inspect current state to see if we are paused
            snapshot = await agent_graph.aget_state(thread_config)
            
            final_state = {}
            
            if snapshot.next:
                # ⏸️ PAUSED: We are at an interrupt!
                logging.info(f"▶️ Resuming interrupted graph. Next nodes: {snapshot.next}")
                
                # Resume with the user's message as the answer
                # Command(resume=value) sends 'value' as the result of the interrupt() call
                res_command = Command(resume=user_message)
                
                final_state = await agent_graph.ainvoke(res_command, thread_config)
                
            else:
                # ▶️ IDLE: Start a new turn
                logging.info("▶️ Starting new graph turn.")
                
                # Check for "Exit" intent to clear history/state if desired?
                # For now, just append to state.
                
                from langchain_core.messages import HumanMessage
                
                initial_input = {
                    "user_goal": user_message,
                    # 📝 Update History Immediately
                    "messages": [HumanMessage(content=user_message)],
                    
                    # 🧹 CLEAR ALL TRANSIENT STATE (Stateless Logic, Persistent Chat)
                    # We accept that memory is persistent (Checkpointer), so we must manually wipe 
                    # the "Logic State" from the previous turn to prevent valid history from polluting new turns.
                    
                    "final_response": None,
                    "error": None,
                    "next_action": None, 
                    "salesforce_data": None,
                    "mcp_results": None, # Fixes Proposal Loop
                    
                    # Fixes "Ghost Workflows" (e.g. Email Builder exiting immediately)
                    "active_workflow": None, 
                    "task_directive": None,
                    "pending_updates": None,
                    
                    # Fixes "Ghost Context" (e.g. "Great news, campaign created" showing up 10 mins later)
                    "email_workflow_context": None,
                    "engagement_workflow_context": None,
                    "save_workflow_context": None,
                    "brevo_results": None,
                    "linkly_links": None,
                    "created_records": None,
                    
                    # Note: We KEEP 'session_context' and 'shared_result_sets' because they might hold 
                    # "found contacts" that the user refers to as "them" in the next turn.
                    # We KEEP 'messages' because that is the Chat History.
                }
                
                # Run the graph (no try/except for GraphInterrupt as it may just return)
                await agent_graph.ainvoke(initial_input, thread_config)
                
                # 🔍 Check resulting state for interrupts
                # 🔍 Check resulting state for interrupts
                snapshot = await agent_graph.aget_state(thread_config)
                final_state = snapshot.values
                
                # DEBUG LOGGING
                logging.info(f"🔎 [Server] Snapshot Next: {snapshot.next}")
                logging.info(f"🔎 [Server] Snapshot Tasks: {len(snapshot.tasks)}")
                if snapshot.tasks:
                     logging.info(f"    Task[0] Interrupts: {snapshot.tasks[0].interrupts}")
                
                if snapshot.tasks and snapshot.tasks[0].interrupts:
                    # 🛑 INTERRUPT DETECTED
                    logging.info("🛑 Graph execution interrupted (detected via snapshot).")
                    
                    interrupt_value = snapshot.tasks[0].interrupts[0].value
                    logging.info(f"   📋 Interrupt Payload: {interrupt_value}")
                    
                    # Send specific response for control messages
                    if isinstance(interrupt_value, str) and ('"type": "confirmation"' in interrupt_value or '"type": "review_proposal"' in interrupt_value):
                        await websocket.send_json(json.loads(interrupt_value))
                    else:
                        # Fallback text response
                        await websocket.send_json({
                            "type": "response",
                            "success": True,
                            "response": str(interrupt_value),
                            "iterations": 0,
                            "salesforce_data": False,
                            "created_records": {},
                            "generated_email_content": None, # Force clean
                            "error": None
                        })
                    continue # Skip standard processing

            # 3. PROCESS RESULT (Logic mostly same as before, but extracting from final_state)
            
            # 🔗 EXTRACT CREATED RECORDS (from persisted state)
            # MemorySaver keeps "created_records" key alive if it was set before.
            # But completion node usually regenerates it from mcp_results of *that* run.
            
            # We need to accumulate them for the UI session if the graph doesn't keep them all defined.
            # Our State definition uses 'merge_dicts' for 'created_records', so it should accumulate!
            
            created_records_map = final_state.get("created_records", {}) or {}
            
            # Filter for UI (remove ' Record' suffix if any)
            filtered_records = {}
            for obj_type, records in created_records_map.items():
                valid_recs = []
                for r in records:
                    name = r.get("Name", "")
                    if name and not name.endswith(" Record"):
                        valid_recs.append(r)
                if valid_recs:
                    filtered_records[obj_type] = valid_recs
            
            final_resp = final_state.get("final_response", "Task completed")
            
            # 📝 INTERRUPT HANDLING IN UI
            # If the graph stops *again* at an interrupt (e.g. asking a question),
            # final_state might reflect the state *at the pause*.
            # The 'final_response' key needs to be set by the node BEFORE calling interrupt().
            # In our logic:
            # prepare_link_node does: state["final_response"] = "Should I...?" THEN calls interrupt().
            # So final_state["final_response"] IS the question. Perfect.
            
            # 🚀 CHECK FOR CONTROL MESSAGES (Review Proposal or Confirmation)
            if isinstance(final_resp, str) and ('"type": "review_proposal"' in final_resp or '"type": "confirmation"' in final_resp):
                await websocket.send_json(json.loads(final_resp))
            else:
                await websocket.send_json({
                    "type": "response",
                    "success": True,
                    "response": final_resp,
                    "iterations": final_state.get("iteration_count", 0),
                    "salesforce_data": bool(final_state.get("salesforce_data")),
                    "created_records": filtered_records,
                    "generated_email_content": final_state.get("generated_email_content"),
                    "error": final_state.get("error")
                })
            
    except WebSocketDisconnect:
        logging.info("Client disconnected")
    except Exception as e:
        logging.error(f"Server Error: {e}")
        import traceback
        logging.error(traceback.format_exc())
        await websocket.send_json({
            "type": "error",
            "message": str(e)
        })
            


if __name__ == "__main__":
    import uvicorn
    import os
    
    # 📝 Print log location for debugging
    log_path = os.path.abspath("agent.log")
    print(f"📝 Logging initialized. File: {log_path}")

    # 🚀 ROBUST LOGGING: Disable Uvicorn's config and attach our handlers manually
    # This prevents Uvicorn from resetting logging settings
    log_config = uvicorn.config.LOGGING_CONFIG
    
    # Run with log_config=None to prevent override
    uvicorn.run(app, host="0.0.0.0", port=8001, log_config=None)

    # Note: Handlers are already attached by logging.basicConfig(force=True) above.
    # Uvicorn loggers (uvicorn, uvicorn.access) will inherit from root logger 
    # if propagation is on (default).
    
    # Explicitly ensure uvicorn loggers use our handlers if needed:
    req_logger = logging.getLogger("uvicorn.access")
    req_logger.handlers = logging.getLogger().handlers 
    err_logger = logging.getLogger("uvicorn.error")
    err_logger.handlers = logging.getLogger().handlers
