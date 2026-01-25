import logging
import json
from langgraph.graph import StateGraph, END
from core.state import MarketingState
from baseagent import execute_single_tool
from typing import Dict, Any, Optional
import urllib.parse

# Constants
BREVO_SERVICE = "Brevo MCP"
SALESFORCE_SERVICE = "Salesforce MCP"

async def create_template_node(state: MarketingState) -> MarketingState:
    """
    Creates the email template in Brevo.
    """
    logging.info("💾 [SaveTemplateWorkflow] Step 1: Creating Brevo Template")
    
    # Initialize save_workflow_context early to ensure state persistence
    if "save_workflow_context" not in state:
        state["save_workflow_context"] = {}
    
    email_data = state.get("generated_email_content")
    # Explicitly clear state (requires generated_email_content NOT to use merge_dicts reducer)
    state["generated_email_content"] = None
    if not email_data:
        state["error"] = "No generated email content found to save."
        logging.error("   ❌ No generated_email_content in state")
        return state
     
    # Prepare args
    args = {
        "template_name": email_data.get("subject", "New Template"),
        "subject": email_data.get("subject", "No subject"),
        "html_content": email_data.get("body_html", "<p>No Content</p>"),
    }

    try:
        res = await execute_single_tool(BREVO_SERVICE, "create_email_template", args)
        
        if res["status"] == "success":
            data = res["data"]
            
            # Handle double-encoded JSON string
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except:
                    pass
            
            if isinstance(data, dict) and "id" in data:
                template_id = data["id"]
                template_name = args["template_name"]  # Use the name we sent
                
                logging.info(f"   ✅ Created Template ID: {template_id}, Name: {template_name}")
                
                # Store both ID and name for picklist value creation
                state["save_workflow_context"]["template_id"] = template_id
                state["save_workflow_context"]["template_name"] = template_name
            else:
                 state["error"] = f"Brevo did not return an ID. Data: {data}"
                 logging.error(f"   ❌ Invalid Brevo response: {data}")
        else:
            state["error"] = f"Failed to create template: {res.get('error')}"
            logging.error(f"   ❌ Brevo tool failed: {res.get('error')}")

    except Exception as e:
        logging.error(f"   ❌ Exception creating template: {e}")
        state["error"] = str(e)
        
    return state
# async def ensure_picklist_value(
#     object_name: str,
#     field_name: str,
#     value: str
# ) -> bool:
#     """
#     Ensure a picklist value exists in Salesforce using Tooling API.
#     Returns True if successful or value already exists.
#     """
#     logging.info(f"   🛠️ Ensuring picklist value '{value}' exists in {object_name}.{field_name}")
    
#     # Derive DeveloperName: Email_template__c -> Email_Template
#     dev_name = field_name.replace("__c", "")
#     if dev_name and dev_name[0].islower():
#         dev_name = dev_name[0].upper() + dev_name[1:]
    
#     try:
#         # Step 1: Query CustomField metadata
#         field_query = f"SELECT Id, Metadata FROM CustomField WHERE TableEnumOrId='{object_name}' AND DeveloperName='{dev_name}'"
#         field_action = f"query/?q={urllib.parse.quote(field_query)}"
        
#         logging.info(f"   🔍 Querying CustomField metadata...")
        
#         field_res = await execute_single_tool(
#             SALESFORCE_SERVICE,
#             "tooling_execute",
#             {
#                 "action": field_action,
#                 "method": "GET"
#             }
#         )
        
#         # Parse response - execute_single_tool wraps it as {'status': 'success', 'data': {'result': '...'}}
#         logging.info(f"   🔍 [Debug] Response keys: {field_res.keys()}")
        
#         # Extract the actual data
#         if "data" in field_res and isinstance(field_res["data"], dict):
#             result_text = field_res["data"].get("result", "")
#         elif "result" in field_res:
#             result_text = field_res["result"]
#         else:
#             logging.error(f"   ❌ Unexpected response structure: {list(field_res.keys())}")
#             return False
        
#         # Extract JSON from result text
#         if isinstance(result_text, str) and "Tooling Execute Result (JSON):" in result_text:
#             json_str = result_text.split("Tooling Execute Result (JSON):")[1].strip()
#             data = json.loads(json_str)
#             logging.info(f"   ✅ Parsed JSON, found {data.get('size', 0)} records")
#         elif isinstance(result_text, dict):
#             data = result_text
#         else:
#             logging.error(f"   ❌ Unexpected response format: {type(result_text)}")
#             logging.error(f"   📋 result_text preview: {str(result_text)[:200]}")
#             return False
        
#         if not data.get("records"):
#             logging.error(f"   ❌ Field {object_name}.{field_name} not found")
#             return False
        
#         record = data["records"][0]
#         metadata = record["Metadata"]
#         field_id = record["Id"]
        
#         logging.info(f"   ✅ Found CustomField: {field_id}")
        
#         # Step 2: Check if it's a picklist
#         field_type = metadata.get("type")
#         if field_type not in ("Picklist", "MultiselectPicklist"):
#             logging.warning(f"   ⚠️ Field is not a picklist (Type: {field_type})")
#             return True
        
#         # Step 3: Check value set
#         value_set = metadata.get("valueSet", {})
        
#         if value_set.get("valueSetName"):
#             logging.warning(f"   ⚠️ Global Value Set detected — manual update required")
#             return False
        
#         definition = value_set.setdefault("valueSetDefinition", {})
#         values = definition.setdefault("value", [])
        
#         # Step 4: Check if value already exists
#         existing_values = [v.get("fullName") for v in values]
        
#         if value in existing_values:
#             logging.info(f"   ✅ Picklist value '{value}' already exists")
#             return True
        
#         # Step 5: Add new value
#         logging.info(f"   ➕ Adding '{value}' to picklist (current: {len(values)} values)...")
        
#         values.append({
#             "fullName": value,
#             "label": value,
#             "default": False,
#             "isActive": True
#         })
        
#         # Step 6: Update via Tooling API
#         update_action = f"sobjects/CustomField/{field_id}"
#         update_payload = {"Metadata": metadata}
        
#         logging.info(f"   📤 Updating CustomField {field_id}...")
#         logging.debug(f"   📋 Payload preview: {json.dumps(update_payload, indent=2)[:500]}")
        
#         try:
#             update_res = await execute_single_tool(
#                 SALESFORCE_SERVICE,
#                 "tooling_execute",
#                 {
#                     "action": update_action,
#                     "method": "PATCH",
#                     "data": update_payload
#                 }
#             )
            
#             logging.info(f"   📥 Update response keys: {update_res.keys()}")
            
#             # Check for errors in response
#             if "error" in update_res:
#                 error_details = update_res["error"]
#                 logging.error(f"   ❌ Tooling API error: {error_details}")
#                 return False
            
#             # Parse the response
#             if "data" in update_res and isinstance(update_res["data"], dict):
#                 result_text = update_res["data"].get("result", "")
#             elif "result" in update_res:
#                 result_text = update_res["result"]
#             else:
#                 # If no error but also no expected result, assume success
#                 logging.info(f"   ✅ Successfully added '{value}' to picklist!")
#                 return True
            
#             # Check if result indicates success
#             if isinstance(result_text, str):
#                 if "successfully" in result_text.lower() or "204" in result_text:
#                     logging.info(f"   ✅ Successfully added '{value}' to picklist!")
#                     return True
#                 elif "error" in result_text.lower():
#                     logging.error(f"   ❌ Update failed: {result_text}")
#                     return False
            
#             logging.info(f"   ✅ Successfully added '{value}' to picklist!")
#             return True
            
#         except Exception as update_error:
#             logging.error(f"   ❌ Update request failed: {type(update_error).__name__}: {update_error}")
            
#             # Try to extract more details from the error
#             error_msg = str(update_error)
#             if "SalesforceApiError" in error_msg:
#                 logging.error(f"   💡 This appears to be a Salesforce API error. Common causes:")
#                 logging.error(f"      • Insufficient permissions to modify metadata")
#                 logging.error(f"      • Field is managed (from a package)")
#                 logging.error(f"      • Invalid metadata structure")
#                 logging.error(f"      • Org has restricted metadata changes")
            
#             return False
        
#     except Exception as e:
#         logging.error(f"   ❌ Picklist update failed: {type(e).__name__}: {e}")
#         import traceback
#         logging.debug(f"   📋 Traceback: {traceback.format_exc()}")
#         return False
def _extract_tooling_json(tool_res: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Extract JSON dict from your tooling_execute wrapper.
    It can return:
      - {"result": "Tooling Execute Result (JSON):\n{...}"}
      - {"data": {"result": "Tooling Execute Result (JSON):\n{...}"}}
      - {"result": {...}} (already dict)
    """
    if not isinstance(tool_res, dict):
        return None

    result_text = None
    if isinstance(tool_res.get("data"), dict):
        result_text = tool_res["data"].get("result")
    else:
        result_text = tool_res.get("result")

    if result_text is None:
        return None

    if isinstance(result_text, dict):
        return result_text

    if isinstance(result_text, str):
        marker = "Tooling Execute Result (JSON):"
        if marker in result_text:
            json_str = result_text.split(marker, 1)[1].strip()
            return json.loads(json_str)

        # sometimes raw JSON text
        s = result_text.strip()
        if s.startswith("{") and s.endswith("}"):
            return json.loads(s)

    return None


async def ensure_picklist_value(object_name: str, field_name: str, value: str) -> bool:
    """
    Ensure a picklist value exists in Salesforce using Tooling API.
    - Works for CustomField picklists
    - Preserves existing values and appends new one
    - Uses valueName (not fullName)
    - Normalizes valueSettings null -> []
    - Sends FullName in PATCH for better reliability
    """
    logging.info(f"🛠️ Ensuring picklist value '{value}' exists in {object_name}.{field_name}")

    # DeveloperName is field api name without __c
    # Email_template__c -> Email_template
    dev_name = field_name.replace("__c", "")

    # 1) Query the CustomField metadata
    field_query = (
        "SELECT Id, Metadata "
        f"FROM CustomField WHERE TableEnumOrId='{object_name}' AND DeveloperName='{dev_name}'"
    )
    field_action = f"query/?q={urllib.parse.quote(field_query)}"

    try:
        field_res = await execute_single_tool(
            SALESFORCE_SERVICE,
            "tooling_execute",
            {"action": field_action, "method": "GET"},
        )
    except Exception as e:
        logging.exception(f"❌ Tooling GET failed: {type(e).__name__}: {e}")
        return False

    data = _extract_tooling_json(field_res)
    if not data:
        logging.error("❌ Could not parse Tooling query response JSON.")
        logging.debug(f"Raw response: {field_res}")
        return False

    records = data.get("records") or []
    if not records:
        logging.error(f"❌ Field not found: {object_name}.{field_name} (DeveloperName={dev_name})")
        return False

    record = records[0]
    field_id = record.get("Id")
    metadata = record.get("Metadata") or {}

    if not field_id:
        logging.error("❌ Tooling response missing CustomField Id.")
        return False

    logging.info(f"✅ Found CustomField: {field_id}")

    # 2) Validate type
    field_type = metadata.get("type")
    if field_type not in ("Picklist", "MultiselectPicklist"):
        logging.warning(f"⚠️ Not a picklist (type={field_type}). Skipping.")
        return True

    # 3) Ensure local value set (not global value set)
    value_set = metadata.get("valueSet") or {}
    metadata["valueSet"] = value_set

    if value_set.get("valueSetName"):
        logging.warning("⚠️ Global Value Set detected (valueSetName not null). Cannot update via CustomField.")
        return False

    # Normalize valueSettings: null -> []
    if value_set.get("valueSettings") is None:
        value_set["valueSettings"] = []

    definition = value_set.get("valueSetDefinition") or {}
    value_set["valueSetDefinition"] = definition

    values = definition.get("value") or []
    definition["value"] = values

    # 4) Check existing values using valueName
    existing_value_names = {v.get("valueName") for v in values if isinstance(v, dict) and v.get("valueName")}
    if value in existing_value_names:
        logging.info(f"✅ Picklist value already exists: '{value}'")
        return True

    # 5) Append new value
    logging.info(f"➕ Adding '{value}' (current={len(values)})")
    values.append({"label": value, "valueName": value, "default": False})

    # 6) PATCH back full metadata
    update_action = f"sobjects/CustomField/{field_id}"
    update_payload = {
        "FullName": f"{object_name}.{field_name}",
        "Metadata": metadata,
    }

    try:
        update_res = await execute_single_tool(
            SALESFORCE_SERVICE,
            "tooling_execute",
            {"action": update_action, "method": "PATCH", "data": update_payload},
        )
    except Exception as e:
        logging.exception(f"❌ Tooling PATCH failed: {type(e).__name__}: {e}")
        return False

    # Your tooling_execute returns {"status":"error"...} on error
    if isinstance(update_res, dict) and update_res.get("status") == "error":
        logging.error(f"❌ Tooling API error: {update_res.get('error')}")
        return False

    # PATCH often returns 204; if no explicit error, treat as success
    logging.info(f"✅ Successfully added/ensured '{value}' on {object_name}.{field_name}")
    return True

async def link_campaign_node(state: MarketingState) -> MarketingState:
    """
    Links the created template to the Salesforce Campaign.
    """
    logging.info("🔗 [SaveTemplateWorkflow] Step 2: Linking to Salesforce Campaign")
    
    ctx = state.get("save_workflow_context", {})
    template_id = ctx.get("template_id")
    template_name = ctx.get("template_name", "Template")
    
    if not template_id:
        logging.warning("   ⚠️ No template ID to link. Skipping.")
        return state
    
    # Create picklist value in format 'templateid-name'
    picklist_value = f"{template_id}-{template_name}"
    
    # ✅ ALWAYS update picklist metadata when we have a template_id
    logging.info(f"   🛠️ Ensuring picklist value '{picklist_value}' exists in Campaign.Email_template__c")
    await ensure_picklist_value("Campaign", "Email_template__c", picklist_value)
    
    # Get Campaign ID from session context or top-level shared_result_sets
    session_context = state.get("session_context", {})
    
    # Try top-level shared_result_sets first
    shared_results = state.get("shared_result_sets", {})
    campaigns = shared_results.get("Campaign") or shared_results.get("campaign", [])

    
    # Fallback to session_context.shared_result_sets
    if not campaigns:
        shared_results = session_context.get("shared_result_sets", {})
        campaigns = shared_results.get("Campaign") or shared_results.get("campaign", [])
    
    if not campaigns:
        logging.warning("   ⚠️ No active campaign found in session. Skipping link.")
        state["final_response"] = f"✅ Template saved to Brevo (ID: {template_id}). Picklist value '{picklist_value}' added to Salesforce, but no active campaign found to link."
        return state
    
    campaign_id = campaigns[0].get("Id")
    if not campaign_id:
        logging.warning("   ⚠️ Campaign record missing ID. Skipping.")
        state["final_response"] = f"✅ Template saved to Brevo (ID: {template_id}). Picklist value '{picklist_value}' added."
        return state
    
    logging.info(f"   🎯 Found Campaign ID: {campaign_id}")
    
    # Upsert Logic - set Email_template__c to the picklist value
    args={
    "object_name": "Campaign",
    "records": [
      {
        "record_id": campaign_id,
        "fields": {
          "Email_template__c": picklist_value
        }
      }
    ]
  }
    # args = {
    #     "object_name": "Campaign",
    #     "record": {
    #         "record_id": campaign_id,
    #         "Email_template__c": picklist_value  # Use the formatted value
    #     } 
    # }
    
    try:
        res = await execute_single_tool(SALESFORCE_SERVICE, "upsert_salesforce_records", args)
        
        if res["status"] == "success":
             logging.info(f"   ✅ Linked Template '{picklist_value}' to Campaign {campaign_id}")
             state["final_response"] = f"✅ Template saved to Brevo (ID: {template_id}) and linked to Salesforce Campaign with value '{picklist_value}'."
        else:
             err = res.get('error', '')
             logging.error(f"   ❌ Failed to link campaign: {err}")
             state["final_response"] = f"✅ Template saved (ID: {template_id}), picklist value added, but failed to link to campaign: {err}"
    
    except Exception as e:
        logging.error(f"   ❌ Exception linking campaign: {e}")
        state["final_response"] = f"✅ Template saved to Brevo (ID: {template_id}), picklist value added, but error linking to Salesforce: {str(e)}"
        
    return state

def build_save_template_workflow():
    builder = StateGraph(MarketingState)
    
    builder.add_node("create_template", create_template_node)
    builder.add_node("link_campaign", link_campaign_node)
    
    builder.set_entry_point("create_template")
    
    builder.add_edge("create_template", "link_campaign")
    builder.add_edge("link_campaign", END)
    
    return builder.compile()
