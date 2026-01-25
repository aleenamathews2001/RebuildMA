
import json

# Mock Final State simulating what comes from email_builder_node
final_state = {
    "generated_email_content": {
        "subject": "Test Subject",
        "body_html": "<p>Test Body</p>",
        "body_text": "Test Body",
        "tone": "Professional",
        "suggested_audience": "Everyone"
    },
    "salesforce_data": {},
    "created_records": {},
    "error": None,
    "final_response": "I've drafted the email."
}

filtered_records = {}

# Simulate server.py payload construction
response_payload = {
    "type": "response",
    "success": True,
    "response": final_state.get("final_response"),
    "iterations": 1,
    "salesforce_data": bool(final_state.get("salesforce_data")),
    "created_records": filtered_records,
    "generated_email_content": final_state.get("generated_email_content"), 
    "error": final_state.get("error")
}

print(json.dumps(response_payload, indent=2))

if response_payload["generated_email_content"] is not None:
    print("SUCCESS: generated_email_content is present.")
else:
    print("FAILURE: generated_email_content is MISSING.")
