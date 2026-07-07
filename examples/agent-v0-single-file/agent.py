import json
import os
from typing import Callable, Dict, Any, List
from openai import OpenAI

api_key = os.environ.get("DEEPSEEK_API_KEY")
if not api_key:
    raise RuntimeError("请先设置环境变量 DEEPSEEK_API_KEY")

client = OpenAI(
    base_url="https://api.deepseek.com",
    api_key=api_key
)


# 定义工具的结构
class ToolDefinition:
    def __init__(self, name: str, description: str, parameters: Dict[str, Any], function: Callable):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.function = function


# read_file 工具的实现
def read_file(path: str) -> str:
    try:
        with open(path, 'r', encoding='utf-8') as file:
            return file.read()
    except Exception as e:
        return f"Error reading file: {str(e)}"


read_file_parameters = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "The path to the file to be read."
        }
    },
    "required": ["path"]
}

read_file_tool = ToolDefinition(
    name="read_file",
    description="读取指定路径的文件内容。当你需要查看文件内容时使用此工具。不要用于目录。",
    parameters=read_file_parameters,
    function=read_file
)
# read_file 工具的定义 END


def to_openai_tool(tool: ToolDefinition) -> Dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        }
    }


conversation = []
tools: List[ToolDefinition] = [read_file_tool]

print("Noval is ready. Type 'exit' to end the conversation.")


def run_v0():
    while True:
        user_input = input("You: ")
        if user_input.lower() == 'exit':
            print("Noval: Goodbye!")
            break
        conversation.append({"role": "user", "content": user_input})
        response = client.chat.completions.create(
            model='deepseek-v4-pro',
            messages=conversation
        )
        print("Server response:", response)
        reply = response.choices[0].message.content
        print(f"Noval: {reply}")
        conversation.append({"role": "assistant", "content": reply})


def run_v1(conversation: List[Dict], tools: List[ToolDefinition]):
    while True:
        user_input = input("You: ")
        if user_input.lower() == 'exit':
            print("Noval: Goodbye!")
            break
        conversation.append({"role": "user", "content": user_input})
        while True:
            response = client.chat.completions.create(
                model='deepseek-v4-pro',
                messages=conversation,
                tools=[to_openai_tool(t) for t in tools],
                tool_choice="auto"
            )
            print("Server response:", response)
            assistant_message = response.choices[0].message
            conversation.append(assistant_message.model_dump())
            if not assistant_message.tool_calls:
                print(f"Noval: {assistant_message.content}")
                break
            for tool_call in assistant_message.tool_calls:
                tool_name = tool_call.function.name
                tool_args = json.loads(tool_call.function.arguments)
                tool_call_id = tool_call.id
                print(f"Noval is calling tool: {tool_name}({json.dumps(tool_args, ensure_ascii=False)})")
                result = None
                for tool in tools:
                    if tool.name == tool_name:
                        result = tool.function(**tool_args)
                        break

                if result is None:
                    result = f"Error: Tool '{tool_name}' not found."
                conversation.append({
                    "role": "tool",
                    "content": str(result),
                    "tool_call_id": tool_call_id
                })


if __name__ == "__main__":
    run_v1(conversation, tools)
