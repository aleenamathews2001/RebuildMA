import logging
import re
from typing import Dict, Any, List
from langgraph.graph import StateGraph, END
from core.state import MarketingState
from baseagent import get_member_dependency, execute_single_tool
from langchain_core.messages import AIMessage

# Constants
LINKLY_SERVICE = "Linkly MCP"
SALESFORCE_SERVICE = "Salesforce MCP"

def _update_mcp_results(state: MarketingState, service_name: str, tool_name: str, result: Dict[str, Any], summary_text: str = None):
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
    }
    
    # Inject summary into 'request' so it appears in Orchestrator progress_summary
    if summary_text:
        tool_res["request"] = {"Summary": summary_text}
        
    service_data["tool_results"].append(tool_res)
    
    # Write back
    mcp_results[service_name] = service_data
    state["mcp_results"] = mcp_results
    return state

async def fetch_missing_data_node(state: MarketingState) -> MarketingState:
    logging.info("🕵️ [EngagementWorkflow] Step 0: Resolving Target Data")
    
    ctx = state.get("engagement_workflow_context", {})
    shared_data = state.get("shared_result_sets", {})
    user_goal = state.get("user_goal", "").lower()
    
    found_campaign_id = None
    campaign_members = []
    
    # ============================================================================
    # CASE 1: User explicitly provides Campaign ID (e.g., "701XXXXXXXXXXXXXXX")
    # ============================================================================
    ids = re.findall(r'[a-zA-Z0-9]{15,18}', state.get("user_goal", ""))
    explicit_campaign_id = next((i for i in ids if i.startswith("701")), None)
    
    if explicit_campaign_id:
        logging.info(f"   🎯 Explicit Campaign ID found: {explicit_campaign_id}")
        found_campaign_id = explicit_campaign_id
    
    # ============================================================================
    # CASE 2: User says "this campaign" → Check shared_result_sets
    # ============================================================================
    elif "this campaign" in user_goal and not found_campaign_id:
        logging.info("   🔍 User said 'this campaign' - checking shared_result_sets")
        
        # Look for campaign in shared data
        campaign_data = shared_data.get("campaign") or shared_data.get("campaigns")
        
        if campaign_data:
            # Handle list or single item
            if isinstance(campaign_data, list) and campaign_data:
                found_campaign_id = campaign_data[0].get("Id")
            elif isinstance(campaign_data, dict):
                found_campaign_id = campaign_data.get("Id")
            
            if found_campaign_id:
                logging.info(f"   ✅ Found Campaign ID from shared data: {found_campaign_id}")
            else:
                logging.warning("   ⚠️ Campaign data exists but no ID found")
        else:
            logging.warning("   ⚠️ User said 'this campaign' but no campaign in shared_result_sets")
    
    # ============================================================================
    # CASE 3: User provides Campaign Name (e.g., "campaign 'Summer Launch'")
    # ============================================================================
    elif not found_campaign_id:
        # Try to extract campaign name from quotes
        name_match = re.search(r'campaign ["\'](.+?)["\']', state.get("user_goal", ""), re.IGNORECASE)
        
        if name_match:
            campaign_name = name_match.group(1)
            logging.info(f"   🔍 Searching for Campaign by name: '{campaign_name}'")
            
            try:
                query = f"SELECT Id, Name FROM Campaign WHERE Name LIKE '%{campaign_name}%' LIMIT 1"
                res = await execute_single_tool(SALESFORCE_SERVICE, "run_dynamic_soql", {"query": query})
                
                if res["status"] == "success" and res["data"]:
                    # Handle wrapped response
                    data = res["data"]
                    if isinstance(data, dict) and "records" in data:
                        records = data["records"]
                    else:
                        records = data
                    
                    if records and len(records) > 0:
                        found_campaign_id = records[0].get("Id")
                        logging.info(f"   ✅ Resolved Campaign name to ID: {found_campaign_id}")
                    else:
                        logging.warning(f"   ⚠️ No campaign found with name: '{campaign_name}'")
                else:
                    logging.warning(f"   ⚠️ Campaign search failed: {res.get('error')}")
            except Exception as e:
                logging.error(f"   ❌ Error searching for campaign: {e}")
    # ============================================================================
    # STEP 2: If we have Campaign ID, fetch CampaignMembers (if not already loaded)
    # ============================================================================
    if found_campaign_id and not campaign_members:
        logging.info(f"   📊 Fetching CampaignMembers for Campaign: {found_campaign_id}")
        
        query = f"""
            SELECT Id, CampaignId, ContactId, Contact.Name, Contact.Email, 
                   LinkId__c, Status 
            FROM CampaignMember 
            WHERE CampaignId = '{found_campaign_id}'
        """
        
        try:
            res = await execute_single_tool(SALESFORCE_SERVICE, "run_dynamic_soql", {"query": query})
            
            if res["status"] == "success":
                # Handle wrapped response
                records_data = res["data"]
                if isinstance(records_data, dict) and "records" in records_data:
                    campaign_members = records_data["records"]
                else:
                    campaign_members = records_data
                
                logging.info(f"   ✅ Fetched {len(campaign_members)} CampaignMembers")
                
                # Log details for debugging
                for idx, member in enumerate(campaign_members):
                    if isinstance(member, dict):
                        link_id = member.get("LinkId__c")
                        status = member.get("Status")
                        
                        # Extract contact info
                        contact = member.get("Contact", {})
                        email = contact.get("Email") if isinstance(contact, dict) else None
                        name = contact.get("Name") if isinstance(contact, dict) else None
                        
                        logging.info(f"   📋 Member {idx + 1}: ID={member.get('Id')}, LinkId={link_id}, Status={status}, Contact={name} ({email})")
                
                # Update MCP results
                _update_mcp_results(state, SALESFORCE_SERVICE, "run_dynamic_soql", res, f"Fetched {len(campaign_members)} members")
            else:
                logging.warning(f"   ⚠️ Failed to fetch members: {res.get('error')}")
        except Exception as e:
            logging.error(f"   ❌ Error fetching members: {e}")
            import traceback
            logging.error(traceback.format_exc())
    
    # ============================================================================
    # STEP 3: Extract Link IDs from CampaignMembers
    # ============================================================================
    target_link_ids = []
    if campaign_members:
        for member in campaign_members:
            if isinstance(member, dict):
                link_id = member.get("LinkId__c")
                if link_id:
                    # Normalize: remove .0 if present
                    link_id_str = str(link_id)
                    if link_id_str.endswith(".0"):
                        link_id_str = link_id_str[:-2]
                    target_link_ids.append(link_id_str)
        
        logging.info(f"   🔗 Extracted {len(target_link_ids)} Link IDs: {target_link_ids}")
    
    # ============================================================================
    # STEP 4: Store results in state
    # ============================================================================
    if campaign_members:
        state.setdefault("shared_result_sets", {})["campaign_members"] = campaign_members
    
    if target_link_ids:
        ctx["target_link_ids"] = target_link_ids
    
    if found_campaign_id:
        ctx["target_campaign_id"] = found_campaign_id
    
    state["engagement_workflow_context"] = ctx
    
    # ============================================================================
    # STEP 5: Validation
    # ============================================================================
    if not found_campaign_id:
        logging.error("   ❌ Could not resolve Campaign ID")
        ctx["error"] = "Could not identify which campaign to track. Please specify a campaign ID or name."
    elif not campaign_members:
        logging.warning("   ⚠️ No CampaignMembers found for this campaign")
        ctx["warning"] = "No members found for this campaign yet."
    elif not target_link_ids:
        logging.warning("   ⚠️ CampaignMembers exist but no LinkId__c values found")
        ctx["warning"] = "Campaign members exist but don't have tracking links assigned yet."
    
    state["engagement_workflow_context"] = ctx
    return state
async def track_clicks_node_v2(state: MarketingState) -> MarketingState:
    logging.info("🔗 [EngagementWorkflow] Step 1: Tracking Clicks via Linkly & Matching to Members")
    
    ctx = state.get("engagement_workflow_context", {})
    target_link_ids = ctx.get("target_link_ids", [])
    target_campaign_id = ctx.get("target_campaign_id")
    
    # Extract CampaignMember data
    shared_data = state.get("shared_result_sets", {})
    
    # Build Link ID → Member mapping
    link_to_member_map = {}  # {link_id: {member_id, email, status}}
    
    potential_lists = [shared_data.get("campaign_members"), shared_data.get("contacts")]
    found_members = []
    
    for lst in potential_lists:
        if lst and isinstance(lst, list) and len(lst) > 0:
            if isinstance(lst[0], dict):
                found_members = lst
                break
    
    if found_members:
        # Update campaign ID if we missed it
        if not target_campaign_id:
             target_campaign_id = found_members[0].get("CampaignId")
             
        for row in found_members:
            if not isinstance(row, dict):
                logging.warning(f"   ⚠️ Skipping non-dict row: {row}")
                continue
            
            # Extract name and email safely
            c_email = None
            c_name = None
            try:
                contact_obj = row.get("Contact")
                if contact_obj and isinstance(contact_obj, dict):
                    c_email = contact_obj.get("Email")
                    c_name = contact_obj.get("Name")
                
                # Fallback: check for flattened fields
                if not c_email:
                    for key in row.keys():
                        if 'email' in key.lower():
                            c_email = row.get(key)
                            break
                            
                if not c_name:
                    for key in row.keys():
                        if 'name' in key.lower() and 'contact' in key.lower():
                             # e.g. Contact.Name
                            c_name = row.get(key)
                            break
                            
            except Exception as e:
                logging.warning(f"   ⚠️ Error extracting contact details: {e}")
            
            member_id = row.get("Id")
            status = row.get("Status")
            link_id = row.get("LinkId__c")
            
            # Map by Link ID (this is the key!)
            if link_id:
                # Normalize: remove .0 if present to match Linkly's integer strings
                lid_str = str(link_id)
                if lid_str.endswith(".0"):
                    lid_str = lid_str[:-2]
                    
                link_to_member_map[lid_str] = {
                    "member_id": member_id,
                    "email": c_email,
                    "name": c_name or "Unknown Member",
                    "status": status
                }
                logging.info(f"   🔗 Mapped Link {link_id} → {c_name} (ID: {member_id})")
        
        logging.info(f"   🗺️ Built Link→Member map with {len(link_to_member_map)} entries")
        logging.info(f"   📊 Link IDs in Salesforce: {list(link_to_member_map.keys())}")
    else:
        logging.warning("   ⚠️ No CampaignMember records found in shared data")
                 
    ctx["link_to_member_map"] = link_to_member_map
    
    # Prepare tracking args
    track_args = {}
    
    if target_link_ids:
        try:
             track_args["link_ids"] = [int(x) for x in target_link_ids if str(x).isdigit()]
        except:
             pass
    
    if not track_args.get("link_ids") and target_campaign_id:
        track_args["campaign_id"] = target_campaign_id
        
    if not track_args:
        logging.error("   ❌ Missing Campaign ID or Link IDs. Cannot track.")
        ctx["error"] = "Missing Campaign ID for engagement tracking."
        state["engagement_workflow_context"] = ctx
        return state

    # Call Linkly to get click data
    try:
        res = await execute_single_tool(LINKLY_SERVICE, "track_link_clicks", track_args)
        logging.info(f"   📊 Linkly response status: {res.get('status')}")
        
        if res["status"] == "success":
            data = res["data"]
            clicks_per_link = data.get("clicks_per_link", {}) or {}
            
            logging.info(f"   🔍 Linkly click data per link: {clicks_per_link}")
            
            # Match clicked links to CampaignMembers
            members_who_clicked = []  # List of {member_id, email, status, link_id, click_count}
            
            for link_id_str, click_count in clicks_per_link.items():
                if click_count > 0:
                    logging.info(f"   🎯 Link {link_id_str} has {click_count} click(s)")
                    
                    # Look up the member who owns this link
                    member_info = link_to_member_map.get(str(link_id_str))
                    
                    if member_info:
                        member_id = member_info.get("member_id")
                        email = member_info.get("email")
                        status = member_info.get("status")
                        
                        c_name = member_info.get("name")
                        
                        members_who_clicked.append({
                            "member_id": member_id,
                            "email": email,
                            "name": c_name,
                            "status": status,
                            "link_id": link_id_str,
                            "click_count": click_count
                        })
                        
                        logging.info(f"   ✅ MATCH FOUND: Link {link_id_str} → Member {member_id} (Email: {email}, Status: {status})")
                    else:
                        logging.warning(f"   ⚠️ Link {link_id_str} has clicks but NO matching CampaignMember found in Salesforce")
                        logging.warning(f"   Available Link IDs in map: {list(link_to_member_map.keys())}")
            
            # Store results
            ctx["members_who_clicked"] = members_who_clicked
            ctx["total_clicks_found"] = sum(clicks_per_link.values())
            
            summary_msg = f"Found {len(members_who_clicked)} member(s) who clicked"
            logging.info(f"   🎉 {summary_msg}")
            
            state = _update_mcp_results(state, LINKLY_SERVICE, "track_link_clicks", res, summary_msg)
            
        elif res.get("status") == "no_clicks":
            logging.info("   ℹ️ No clicks recorded yet.")
            ctx["members_who_clicked"] = []
            ctx["total_clicks_found"] = 0
        else:
            msg = f"Tracking failed: {res.get('error') or res.get('message')}"
            logging.warning(f"   ⚠️ {msg}")
            ctx["error"] = msg
            ctx["members_who_clicked"] = []

    except Exception as e:
        ctx["error"] = f"Exception tracking clicks: {e}"
        ctx["members_who_clicked"] = []
        logging.error(f"   ❌ Exception: {e}")
        import traceback
        logging.error(traceback.format_exc())
        
    state["engagement_workflow_context"] = ctx
    return state

async def update_engagement_node(state: MarketingState) -> MarketingState:
    logging.info("☁️ [EngagementWorkflow] Step 2: Updating CampaignMember Status in Salesforce")
    
    ctx = state.get("engagement_workflow_context", {})
    members_who_clicked = ctx.get("members_who_clicked", [])
    
    if not members_who_clicked:
        logging.info("   ℹ️ No members to update (no clicks found).")
        ctx["update_summary"] = "No clicks detected, no updates needed."
        state["engagement_workflow_context"] = ctx
        return state
        
    records_to_update = []
    
    for member_data in members_who_clicked:
        member_id = member_data.get("member_id")
        email = member_data.get("email")
        current_status = member_data.get("status")
        link_id = member_data.get("link_id")
        click_count = member_data.get("click_count")
        
        # Only update if status is NOT already "Responded"
        if current_status != "Responded":
            records_to_update.append({
                "record_id": member_id,
                "fields": {
                    "Status": "Responded"
                }
            })
            logging.info(f"   📝 Queuing update: Member {member_id} (Email: {email}, Link: {link_id}) - {current_status} → Responded ({click_count} clicks)")
        else:
            logging.info(f"   ⏭️ Skipping Member {member_id} (Email: {email}): already Responded")

    if not records_to_update:
        logging.info("   ℹ️ All clicked members already have 'Responded' status.")
        ctx["update_summary"] = "All members already marked as Responded."
        state["engagement_workflow_context"] = ctx
        return state
        
    logging.info(f"   🚀 Updating {len(records_to_update)} CampaignMember record(s)...")

    upsert_args = {
        "object_name": "CampaignMember",
        "records": records_to_update
    }
    
    try:
        res = await execute_single_tool(SALESFORCE_SERVICE, "upsert_salesforce_records", upsert_args)
        
        if res["status"] == "success":
            logging.info(f"   ✅ Successfully updated {len(records_to_update)} CampaignMember(s) to 'Responded'")
            state = _update_mcp_results(state, SALESFORCE_SERVICE, "upsert_salesforce_records", res, f"Updated {len(records_to_update)} records to Responded")
            ctx["update_summary"] = f"Successfully updated {len(records_to_update)} CampaignMembers to 'Responded'."
            ctx["updated_count"] = len(records_to_update)
        else:
            logging.warning(f"   ⚠️ Salesforce update failed: {res.get('error')}")
            ctx["update_error"] = res.get("error")
            ctx["update_summary"] = f"Update failed: {res.get('error')}"

    except Exception as e:
        logging.error(f"   ❌ Salesforce update exception: {e}")
        ctx["update_error"] = str(e)
        ctx["update_summary"] = f"Update exception: {e}"

    state["engagement_workflow_context"] = ctx
    return state

async def summary_node(state: MarketingState) -> MarketingState:
    """
    Final node to generate summary and ensure workflow termination.
    """
    logging.info("🏁 [EngagementWorkflow] Step 3: Summary & Completion")
    
    ctx = state.get("engagement_workflow_context", {})
    
    # Gather stats
    members_who_clicked = ctx.get("members_who_clicked", [])
    total_clicks = ctx.get("total_clicks_found", 0)
    update_summary = ctx.get("update_summary", "No updates performed.")
    updated_count = ctx.get("updated_count", 0)
    
    error = ctx.get("error")
    update_error = ctx.get("update_error")
    
    # Build detailed message
    msg = ""
    
    if members_who_clicked:
        # Group by status to communicate updates clearly
        updated_members = []
        already_responded = []
        
        for member in members_who_clicked:
            m_id = member.get("member_id")
            name = member.get("name") or "Member"
            
            # Format as user-friendly hyperlink for LWC
            link_text = f"[{name}](/{m_id})"
            
            original_status = member.get("status")
            if original_status != "Responded":
                updated_members.append(f"• {link_text}")
            else:
                already_responded.append(f"• {link_text}")
        
        # Construct natural language message
        msg += f"Good news! I found {total_clicks} click(s) for this campaign.\n\n"
        
        if updated_members:
            msg += "I've successfully updated the status to 'Responded' for:\n"
            msg += "\n".join(updated_members) + "\n\n"
            
        if already_responded:
            if updated_members:
                msg += "The following members also clicked, but were already marked as 'Responded':\n"
            else:
                msg += "The following members clicked, but they were already marked as 'Responded':\n"
            msg += "\n".join(already_responded)
        
    else:
        msg = "I checked for engagement, but I didn't find any clicks for this campaign yet."

    if error:
        msg += f"\n\n⚠️ {error}"
    if update_error:
        msg += f"\n\n⚠️ Update Issue: {update_error}"
        
    logging.info(f"\n{msg}")
    
    # Return AIMessage to signal completion
    return {**state, "messages": [AIMessage(content=msg)]}

def build_engagement_workflow():
    builder = StateGraph(MarketingState)
    
    builder.add_node("fetch_data", fetch_missing_data_node)
    builder.add_node("track_clicks", track_clicks_node_v2)
    builder.add_node("update_engagement", update_engagement_node)
    builder.add_node("summary_node", summary_node)

    # Set Entry Point
    builder.set_entry_point("fetch_data")
    
    # Linear Flow
    builder.add_edge("fetch_data", "track_clicks")
    builder.add_edge("track_clicks", "update_engagement")
    builder.add_edge("update_engagement", "summary_node")
    builder.add_edge("summary_node", END)

    return builder.compile()
 