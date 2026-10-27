from unittest.mock import MagicMock, patch
from nano_agent.core import NanoAgent
import os
import sys

# Add project root to sys.path to allow importing nano_agent
sys.path.append("/Users/jonathanwallace/Projects/nano-agent")

def test_discovery_and_loop():
    tools_dir = "/Users/jonathanwallace/Projects/nano-agent/tools"
    
    # Patch OpenAI where it is USED, not where it is defined
    with patch('nano_agent.core.OpenAI') as MockOpenAI:
        mock_client = MockOpenAI.return_value
        
        mock_response_1 = MagicMock()
        mock_message_1 = MagicMock()
        mock_tool_call = MagicMock()
        mock_tool_call.function.name = "list_files"
        mock_tool_call.function.arguments = "{'path': '.'}"
        mock_tool_call.id = "call_123"
        
        mock_message_1.tool_calls = [mock_tool_call]
        mock_message_1.content = None
        mock_response_1.choices = [mock_message_1]
        
        mock_response_2 = MagicMock()
        mock_message_2 = MagicMock()
        mock_message_2.tool_calls = None
        mock_message_2.content = "I found the files!"
        mock_response_2.choices = [mock_message_2]
        
        mock_client.chat.completions.create.side_effect = [mock_response_1, mock_response_2]
        
        agent = NanoAgent(tools_dir=tools_dir)
        
        print(f"Discovered tools: {list(agent.registry.tools.keys())}")
        assert "list_files" in agent.registry.tools
        
        result = agent.run("What files are here?")
        print(f"Final result: {result}")
        assert result == "I found the files!"
        assert mock_client.chat.completions.create.call_count == 2
        print("Test Passed!")

if __name__ == "__main__":
    test_discovery_and_loop()
