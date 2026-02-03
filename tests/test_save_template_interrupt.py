import unittest
from unittest.mock import patch, MagicMock
import sys
import os
import asyncio

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from workflows.save_template_workflow import upsert_link_node

class TestSaveTemplateInterrupt(unittest.TestCase):
    
    @patch('workflows.save_template_workflow.interrupt')
    @patch('workflows.save_template_workflow.execute_single_tool')
    def test_skip_interrupt_when_no_campaign(self, mock_execute, mock_interrupt):
        # Setup state with NO campaign_id
        state = {
            "save_workflow_context": {
                "template_id": "123", 
                # "campaign_id": None  <-- Missing
            },
            "final_response": "Saved template."
        }
        
        # Run node
        result_state = asyncio.run(upsert_link_node(state))
        
        # Verify interrupt was NOT called
        mock_interrupt.assert_not_called()
        print("\n✅ Verified: Interrupt skipped when campaign_id is missing.")

    @patch('workflows.save_template_workflow.interrupt')
    @patch('workflows.save_template_workflow.execute_single_tool')
    def test_trigger_interrupt_when_campaign_present(self, mock_execute, mock_interrupt):
        # Setup state WITH campaign_id
        state = {
            "save_workflow_context": {
                "template_id": "123",
                "campaign_id": "camp_001",
                "picklist_value": "123-Template"
            },
            "final_response": "Confirm linking?"
        }
        
        # Mock user saying "yes"
        mock_interrupt.return_value = "yes"
        
        # Mock successfulupsert
        mock_execute.return_value = {"status": "success"}

        # Run node
        result_state = asyncio.run(upsert_link_node(state))
        
        # Verify interrupt WAS called
        mock_interrupt.assert_called_once()
        print("\n✅ Verified: Interrupt triggered when campaign_id is present.")

if __name__ == '__main__':
    unittest.main()
