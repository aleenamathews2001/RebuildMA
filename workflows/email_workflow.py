import logging
import re
import json
from typing import Dict, Any, List
from langgraph.graph import StateGraph, END
from core.state import MarketingState
from baseagent import get_member_dependency, execute_single_tool

# Constants
# Constants
BREVO_SERVICE = "Brevo MCP"
LINKLY_SERVICE = "Linkly MCP"
SALESFORCE_SERVICE = "Salesforce MCP"

def _update_mcp_results(state: MarketingState, service_name: str, tool_name: str, result: Dict[str, Any]):
    """
    Manually update mcp_results so the Orchestrator sees the work.
    """
    mcp_results = state.get("mcp_results", {}) or {}
    service_data = mcp_results.get(service_name, {"execution_summary": {}, "tool_results": []})
    
    # Update stats
    summary = service_data.get("execution_summary", {})
    summary["total_calls"] = summary.get("total_calls", 0) + 1
    if result.get("status") == "success":
        summary["successful_calls"] = summary.get("successful_calls", 0) + 1
    else:
        summary["failed_calls"] = summary.get("failed_calls", 0) + 1
    service_data["execution_summary"] = summary
        
    # Append result
    tool_res = {
        "tool_name": tool_name,
        "status": result.get("status", "unknown"),
        "response": result.get("data", str(result)), 
        # approximate structure for Orchestrator summarizer
    }
    service_data["tool_results"].append(tool_res)
    
    # Write back
    mcp_results[service_name] = service_data
    state["mcp_results"] = mcp_results
    return state

async def preview_template_node(state: MarketingState) -> MarketingState:
    """
    1. Previews the email template using Brevo MCP.
    2. Stores the preview result for link analysis.
    """
    logging.info("🚀 [EmailWorkflow] Step 1: Preview Template")
    
    shared_data = state.get("shared_result_sets", {})
    
    # Extract Campaign and Contact data
    campaign_id = None
    template_id = None
    contacts = []
    
    if "campaign" in shared_data:
        campaigns = shared_data["campaign"]
        if campaigns:
             campaign_data = campaigns[0]
             campaign_id = campaign_data.get("Id")
             template_id = campaign_data.get("Email_template__c")
             
             # Fallback if field name is different
             if not template_id:
                 template_id = campaign_data.get("description") # sometimes stored here in testing?
            
             # Clean field: Extract integer if format is "3 - Name"
             if template_id:
                 tid_str = str(template_id)
                 if not tid_str.isdigit():
                     # Try to match starting digits
                     match = re.match(r'^(\d+)', tid_str)
                     if match:
                         template_id = match.group(1)
                         logging.info(f"   🧹 Cleaned Template ID '{tid_str}' to '{template_id}'")
                     else:
                         logging.warning(f"   ⚠️ Could not extract integer ID from '{tid_str}'")
                         template_id = None
     
    # Also check if template_id was passed directly in the user request or previous context?
    # For now, rely on Salesforce data.
             
    if "contacts" in shared_data:
        contacts = shared_data["contacts"]
        
    logging.info(f"   Campaign ID: {campaign_id}, Template ID: {template_id}, Contacts: {len(contacts)}")

    if not template_id:
        msg = "❌ Missing Template ID in campaign data"
        logging.error(msg)
        state["error"] = msg
        return state

    if not contacts:
        msg = "❌ No contacts found in result set 'contacts'"
        logging.error(msg)
        state["error"] = msg
        return state

    # Sample preview for link detection (using first contact)
    sample_contact = contacts[0]
    preview_args = {
        "template_id": int(template_id),
        "recipients": [{"email": sample_contact.get("Email"), "name": sample_contact.get("FirstName")}]
    }

    try:
        result = await execute_single_tool(BREVO_SERVICE, "preview_email", preview_args)
        
        if result["status"] == "success":
            # Store necessary context in temporary state fields
            # We add them to shared_result_sets or a temporary stash?
            # MarketingState has 'mcp_results' which is good.
            # But let's use a specific key for this workflow data
            email_ctx = {
                "template_id": int(template_id),
                "contacts": contacts,
                "preview_data": result["data"],
                "campaign_id": campaign_id,
                "campaign_name": campaign_data.get("Name")
            }
            state.setdefault("email_workflow_context", {}).update(email_ctx)
            logging.info("   ✅ Preview successful")
        else:
            state["error"] = f"Preview failed: {result.get('error')}"

    except Exception as e:
        logging.error(f"Failed to preview: {e}")
        state["error"] = str(e)

    return state

async def analyze_links_node(state: MarketingState) -> MarketingState:
    logging.info("🔍 [EmailWorkflow] Step 2: Analyzing Links")
    
    ctx = state.get("email_workflow_context", {})
    preview_data = ctx.get("preview_data", {})
    logging.info(f"ctx,{ctx} and preview data: {preview_data}")
    has_links = False
    found_urls = []
    template_params = set()
    
    if preview_data and "previews" in preview_data:
        html_content = preview_data["previews"][0].get("html_content", "")
        # Regex to find links
        # Looking for href="http..." or https...
        import re
        urls = re.findall(r'href=[\'"]?(https?://[^\'" >]+)', html_content)
        
        # Regex to find {{ params.Name }}
        # Common Brevo format: {{ params.FirstName }} or {{params.FirstName}}
        # Match alphanumeric and underscores
        found_params = re.findall(r'\{\{\s*params\.([a-zA-Z0-9_]+)\s*\}\}', html_content)
        if found_params:
            template_params.update(found_params)
            logging.info(f"   📝 Found template params: {template_params}")
        
        # Filter out unsubscribes/utility links if needed?
        # For now, any link triggers the shortener.
        cleaned_urls = [u for u in urls if "unsubscribe" not in u.lower()]
        
        if cleaned_urls:
            has_links = True
            found_urls = list(set(cleaned_urls)) # dedupe
            logging.info(f"   🔗 Found {len(found_urls)} unique links: {found_urls}")
    
    ctx["has_links"] = has_links
    ctx["found_urls"] = found_urls
    ctx["template_params"] = list(template_params)
    
    state["email_workflow_context"] = ctx
    return state

async def link_shortener_node(state: MarketingState) -> MarketingState:
    logging.info("🔗 [EmailWorkflow] Step 3: Linkly Shortening")
    
    ctx = state.get("email_workflow_context", {})
    contacts = ctx.get("contacts", [])
    found_urls = ctx.get("found_urls", []) # List of URLs to shorten
    campaign_id = ctx.get("campaign_id")

    if not found_urls:
        logging.info("   No URLs to shorten.")
        return state

    # Prepare inputs for generate_uniqueurl
    linkly_contacts = []
    for c in contacts:
        linkly_contacts.append({
            "email": c.get("Email"),
            "name": c.get("FirstName"),
        })
    
    gen_args = {
        "campaign_id": campaign_id,
        "contacts": linkly_contacts,
        "urls": found_urls
    }

    short_links_map = {} # {contact_id: {original: {short_url, link_id}}}
    
    try:
        res = await execute_single_tool(LINKLY_SERVICE, "generate_uniqueurl", gen_args)
        
        if res["status"] == "success":
            data = res["data"]
            results = data.get("results", [])
            logging.info(f"   ✅ Batch generation complete. Processed {len(results)} contacts.")

            # Map back to Contact IDs
            email_to_cid = {c.get("Email"): c.get("Id") for c in contacts if c.get("Email")}
            
            for item in results:
                c_email = item.get("contact", {}).get("email")
                c_id = email_to_cid.get(c_email)
                
                if c_id:
                    links = item.get("links", [])
                    contact_links = {}
                    for l in links:
                        # Linkly tool returns dicts, check for success
                        if l.get("status") == "success":
                            orig = l.get("original_url")
                            short = l.get("short_url")
                            lid = l.get("link_id")
                            if orig:
                                contact_links[orig] = {"short_url": short, "link_id": lid}
                    
                    short_links_map[c_id] = contact_links
                else:
                    logging.warning(f"   ⚠️ Could not map Linkly result for {c_email} back to a Contact ID")

            ctx["short_links_map"] = short_links_map
            state = _update_mcp_results(state, LINKLY_SERVICE, "generate_uniqueurl", res)
            
        else:
             logging.error(f"   ❌ Link generation failed: {res.get('error')}")
             state["error"] = f"Link generation failed: {res.get('error')}"

    except Exception as e:
        logging.error(f"   ❌ Exception in link shortener: {e}")
        state["error"] = str(e)
    
    state["email_workflow_context"] = ctx
    return state

async def send_email_node(state: MarketingState) -> MarketingState:
    logging.info("📧 [EmailWorkflow] Step 4: Sending Emails via Brevo")
    
    ctx = state.get("email_workflow_context", {})
    contacts = ctx.get("contacts", [])
    template_id = ctx.get("template_id")
    short_links_map = ctx.get("short_links_map", {})
    
    if not contacts or not template_id:
        return state

    template_params = ctx.get("template_params", [])
    
    # Prepare recipients for batch sending
    recipients = []
    logging.info(f"contact email send ,{len(contacts)}")
    for contact in contacts:
        c_id = contact.get("Id")
        c_email = contact.get("Email")
        c_name = contact.get("FirstName")
        
        # Prepare params dynamically
        params = {}
        if template_params:
            for key in template_params:
                # 1. Exact match
                val = contact.get(key)
                # 2. Case-insensitive match
                if val is None:
                    for k, v in contact.items():
                        if k.lower() == key.lower():
                            val = v
                            break
                if val:
                    params[key] = val
        else:
            # Fallback for backward compatibility
            params["FirstName"] = c_name
            params["FirstName "] = c_name 

        
        # Inject short links if available
        if c_id in short_links_map:
            links = short_links_map[c_id]
            # links is { original_url: {short_url, link_id} } or empty
            
            if links:
                # Get first link data
                first_val = list(links.values())[0]
                if isinstance(first_val, dict):
                    short_url = first_val.get("short_url")
                else:
                    short_url = first_val # Fallback
                
                if short_url:
                    params["LINK"] = short_url
        
        recipient = {
            "email": c_email,
            "name": c_name or "", 
            "params": params
        }
        recipients.append(recipient)

    # Call Send Batch
    send_args = {
        "template_id": int(template_id),
        "recipients": recipients,
        "sender_email": "aleenamathews2001@gmail.com", 
        "sender_name": "Aleena Mathews"
    }
    
    try:
        res = await execute_single_tool(BREVO_SERVICE, "send_batch_emails", send_args)
        if res["status"] == "success":
            logging.info("   ✅ Batch email sent successfully")
            ctx["send_result"] = res["data"]
            state = _update_mcp_results(state, BREVO_SERVICE, "send_batch_emails", res)
        else:
            state["error"] = f"Send failed: {res.get('error')}"
            logging.error(f"   ❌ Send failed: {res.get('error')}")
    except Exception as e:
        state["error"] = f"Send Exception: {e}"
        logging.error(f"   ❌ Send Exception: {e}")

    state["email_workflow_context"] = ctx
    return state

async def update_salesforce_node(state: MarketingState) -> MarketingState:
    logging.info("☁️ [EmailWorkflow] Step 5: Updating Salesforce Status")
    
    ctx = state.get("email_workflow_context", {})
    contacts = ctx.get("contacts", [])
    campaign_id = ctx.get("campaign_id")
    short_links_map = ctx.get("short_links_map", {})
    
    # We need to update CampaignMember status.
    # Record structure: {CampaignId, ContactId, Status="Sent", ...}
    
    contact_id_to_member_id = {}
    already_has_members = False

    # Check if contacts are actually CampaignMember objects (have ContactId)
    if contacts and isinstance(contacts[0], dict) and contacts[0].get("ContactId"):
         logging.info("   ℹ️ Input contacts appear to be CampaignMember records. Using existing IDs.")
         for c in contacts:
             c_id = c.get("ContactId")
             m_id = c.get("Id")
             if c_id and m_id:
                 contact_id_to_member_id[c_id] = m_id
         already_has_members = True

    if not already_has_members:
        # 1. Fetch CampaignMember IDs needed for update
        logging.info("   🔍 Fetching CampaignMember IDs for update...")
        
        try:
            soql = f"SELECT Id, ContactId FROM CampaignMember WHERE CampaignId = '{campaign_id}'"
            soql_args = {"query": soql}
            
            current_members_res = await execute_single_tool(SALESFORCE_SERVICE, "run_dynamic_soql", soql_args)
            
            if current_members_res["status"] == "success":
                data = current_members_res["data"]
                rows = []
                
                # Handle SOQL response structure (dict with 'records' or direct list)
                if isinstance(data, dict):
                    rows = data.get("records", [])
                elif isinstance(data, list):
                    rows = data
                else:
                    logging.warning(f"   ⚠️ Unexpected SOQL result format type: {type(data)}")

                if rows:
                    for row in rows:
                        c_id = row.get("ContactId")
                        m_id = row.get("Id")
                        if c_id and m_id:
                            contact_id_to_member_id[c_id] = m_id
                    logging.info(f"   ✅ Found {len(contact_id_to_member_id)} CampaignMember records.")
                else:
                     logging.warning(f"   ⚠️ No records found or unexpected format: {data}")
            else:
                 error_msg = current_members_res.get('error')
                 logging.error(f"   ❌ Failed to query CampaignMembers: {error_msg}")
                 state = _update_mcp_results(state, SALESFORCE_SERVICE, "run_dynamic_soql", current_members_res)
                 state["error"] = error_msg
                 
        except Exception as e:
            logging.error(f"   ❌ Exception querying CampaignMembers: {e}")
            state["error"] = str(e)

    # 2. Build Upsert Payload
    records_to_update = []
    
    for contact in contacts:
        c_id = contact.get("Id")
        member_id = contact_id_to_member_id.get(c_id)
        
        if not member_id:
            logging.warning(f"   ⚠️ No CampaignMember found for Contact {c_id}, skipping status update.")
            continue

        fields = {
            "Status": "Sent"
        }
        
        # Add link tracking data
        if c_id in short_links_map:
            links = short_links_map[c_id]
            if links:
                first_val = list(links.values())[0]
                short_url = first_val.get("short_url")
                link_id = first_val.get("link_id")
                
                if short_url:
                    fields["Link__c"] = short_url
                if link_id:
                    try:
                         fields["LinkId__c"] = float(link_id)
                    except:
                         fields["LinkId__c"] = link_id

        records_to_update.append({
            "record_id": member_id,
            "fields": fields
        })
    logging.info(f"   ✅ Found {len(records_to_update)} records to update.")
    records_to_upsert = records_to_update
    logging.info(f"   ✅ Found {len(records_to_upsert)} records to upsert.")
        
    if not records_to_upsert:
        return state

    # Batch Upsert
    upsert_args = {
        "object_name": "CampaignMember",
        "records": records_to_upsert
    }
    
    try:
        res = await execute_single_tool(SALESFORCE_SERVICE, "upsert_salesforce_records", upsert_args)
        
        if res["status"] == "success":
            raw_data = res["data"]
            # upsert tool returns json string
            if isinstance(raw_data, str):
                try:
                    upsert_result = json.loads(raw_data)
                except:
                    upsert_result = raw_data
            else:
                upsert_result = raw_data
                
            if isinstance(upsert_result, dict):
                if upsert_result.get("success"):
                     logging.info(f"   ✅ Salesforce updated successfully: {upsert_result.get('successful')} ok, {upsert_result.get('failed')} failed.")
                else:
                     logging.warning(f"   ⚠️ Salesforce update reported failure: {upsert_result.get('error')}")
            
            state = _update_mcp_results(state, SALESFORCE_SERVICE, "upsert_salesforce_records", res)
        else:
             logging.warning(f"   ⚠️ Salesforce update warning: {res.get('error')}")
             state = _update_mcp_results(state, SALESFORCE_SERVICE, "upsert_salesforce_records", res)
             state["error"] = res.get('error')

    except Exception as e:
        logging.error(f"Salesforce update failed: {e}")
        state["error"] = str(e)
        
    return state


def build_email_workflow():
    builder = StateGraph(MarketingState)
    
    builder.add_node("preview_template", preview_template_node)
    builder.add_node("analyze_links", analyze_links_node)
    builder.add_node("link_shortener", link_shortener_node)
    builder.add_node("send_email", send_email_node)
    builder.add_node("update_salesforce", update_salesforce_node)

    builder.set_entry_point("preview_template")

    # Conditional logic
    def check_links(state):
        result = state.get("email_workflow_context", {}).get("has_links", False)
        return "link_shortener" if result else "send_email"

    builder.add_conditional_edges("analyze_links", check_links, {
        "link_shortener": "link_shortener",
        "send_email": "send_email"
    })
    
    builder.add_edge("preview_template", "analyze_links")
    builder.add_edge("link_shortener", "send_email")
    builder.add_edge("send_email", "update_salesforce")
    builder.add_edge("update_salesforce", END)

    return builder.compile()
