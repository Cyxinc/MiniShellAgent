#!/usr/bin/env python
# coding=utf-8

import json
import re
from typing import Optional, List, Dict, Any
from .models import BaseLLM
from .tools import TerminalTool
from .prompts import (
    COMPLETE_SYSTEM_PROMPT, COMPLETE_USER_TEMPLATE,
    CHAT_SYSTEM_PROMPT,
    get_agent_system_prompt, AGENT_USER_TEMPLATE, AGENT_OBSERVATION_TEMPLATE
)
from .ui import ui
from .config import Config

class BaseAgent:
    def __init__(self, llm: BaseLLM):
        self.llm = llm
        self.history: List[Dict[str, str]] = []
    
    def add_to_history(self, role: str, content: str):
        self.history.append({"role": role, "content": content})
    
    def clear_history(self):
        self.history = []


class CompleteAgent(BaseAgent):
    def __init__(self, llm: BaseLLM):
        super().__init__(llm)
        self.system_prompt = COMPLETE_SYSTEM_PROMPT
    
    @staticmethod
    def load_recent_history(max_lines: int = None) -> List[str]:
        """Load recent terminal history, prioritize HISTFILE env var, compatible with zsh `: ts:0;cmd` format."""
        from collections import deque
        import os
        from pathlib import Path

        max_lines = max_lines or Config.HISTORY_MAX_LINES
        history_file_env = os.getenv("HISTFILE")
        history_path = os.path.expanduser(history_file_env) if history_file_env else os.path.expanduser(Config.HISTORY_FILE)
        if not os.path.exists(history_path):
            return []

        recent_lines: deque[str] = deque(maxlen=max_lines)
        try:
            with open(history_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith(": ") and ";" in line:
                        # zsh history with timestamp
                        cmd = line.split(";", 1)[1]
                    else:
                        cmd = line
                    if cmd:
                        recent_lines.append(cmd)
        except Exception:
            return []
        # Return from recent to old (reversed), deduplicate while preserving order
        seen = set()
        ordered = []
        for cmd in reversed(recent_lines):
            if cmd in seen:
                continue
            seen.add(cmd)
            ordered.append(cmd)
        return ordered

    def complete(self, user_input: str, max_suggestions: int = 5, silent: bool = False) -> List[str]:
        if not user_input.strip():
            return []
        if not silent:
            ui.print_thinking("Generating command suggestions...")
        history_lines = self.load_recent_history()
        history_text = "\n".join(history_lines) if history_lines else "(no history)"
        prompt = COMPLETE_USER_TEMPLATE.format(user_input=user_input, history=history_text)
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": prompt}
        ]
        
        try:
            response = self.llm.generate(messages, temperature=0.3)
            # Complete mode doesn't show token statistics
            commands = []
            for line in response.strip().split('\n'):
                line = line.strip()
                if line.startswith('```'):
                    continue
                line = re.sub(r'^\d+[\.\)]\s*', '', line)
                if line and not line.startswith('#'):
                    commands.append(line)
            return commands[:max_suggestions]
        except Exception as e:
            # When completion fails, show error even if silent=True, otherwise user won't know what happened
            # But only show detailed stack trace in verbose mode
            ui.print_error(f"Completion failed: {str(e)}")
            if Config.VERBOSE:
                import traceback
                ui.console.print(traceback.format_exc(), style="dim")
            return []


class ChatAgent(BaseAgent):
    def __init__(self, llm: BaseLLM):
        super().__init__(llm)
        self.system_prompt = CHAT_SYSTEM_PROMPT
        self.add_to_history("system", self.system_prompt)
    
    def chat(self, user_input: str) -> str:
        if not user_input.strip():
            return "Please enter your question."
        
        self.add_to_history("user", user_input)
        ui.print_thinking()
        
        try:
            response = self.llm.generate(self.history, temperature=0.7)
            # Display token statistics
            stats = self.llm.get_token_stats()
            if stats.get("call_count", 0) > 0:
                ui.print_token_stats(self.llm.get_model_name(), stats)
            self.add_to_history("assistant", response)
            return response
        except Exception as e:
            ui.print_error(f"Chat failed: {str(e)}")
            return f"Sorry, an error occurred: {str(e)}"


class CommandAgent(BaseAgent):
    def __init__(
        self, 
        llm: BaseLLM,
        max_steps: Optional[int] = None,
        require_confirm: bool = True,
        auto_mode: bool = False,
        max_idle_steps: Optional[int] = None,
    ):
        super().__init__(llm)
        self.tool = TerminalTool()
        self.max_steps = max_steps or Config.MAX_STEPS
        self.require_confirm = require_confirm
        self.auto_mode = auto_mode
        self.max_idle_steps = max_idle_steps or Config.MAX_IDLE_STEPS
        # Select different prompts based on auto_mode
        self.system_prompt = get_agent_system_prompt(auto_mode=auto_mode)  # Dynamically get, includes latest environment info
        self.add_to_history("system", self.system_prompt)
    
    def run(self, task: Optional[str] = None, continue_execution: bool = False) -> Dict[str, Any]:
        """
        Run task
        
        Args:
            task: Task description, if None and continue_execution=True, continue execution
            continue_execution: Whether to continue execution (don't add new task prompt)
        """
        if not continue_execution and task:
            prompt = AGENT_USER_TEMPLATE.format(task=task)
            self.add_to_history("user", prompt)
            ui.print_separator()
        elif continue_execution:
            # Continue execution mode, don't add new prompt, continue directly
            pass
        else:
            # Neither task nor continue mode, return error
            return {
                "success": False,
                "error": "No task provided"
            }
        
        steps = []
        current_step = 0
        idle_steps = 0
        
        while current_step < self.max_steps:
            current_step += 1
            ui.print_thinking("分析任务...")
            
            try:
                try:
                    # Call LLM, use timeout from config
                    # Note: If this hangs, it might be a network or API issue
                    response = self.llm.generate(
                        self.history, 
                        temperature=0.5, 
                        timeout=Config.LLM_TIMEOUT
                    )
                    # Display token statistics
                    stats = self.llm.get_token_stats()
                    if stats.get("call_count", 0) > 0:
                        ui.print_token_stats(self.llm.get_model_name(), stats)
                except Exception as llm_error:
                    # LLM call failed (network error, API error, etc.)
                    error_type = type(llm_error).__name__
                    error_msg = str(llm_error)
                    ui.print_error(f"LLM call failed ({error_type}): {error_msg}")
                    
                    if Config.VERBOSE:
                        import traceback
                        ui.print_error("Detailed error information:")
                        ui.console.print(traceback.format_exc(), style="dim")
                    
                    idle_steps += 1
                    if idle_steps >= self.max_idle_steps:
                        ui.print_warning("Consecutive LLM call failures, automatically terminated.")
                        ui.print_separator()
                        return {
                            "success": False,
                            "steps": steps,
                            "error": f"LLM call failed: {error_msg}"
                        }
                    continue
                
                # Check if response is empty
                if response is None:
                    ui.print_error("LLM 返回了 None 响应")
                    idle_steps += 1
                    if idle_steps >= self.max_idle_steps:
                        ui.print_warning("连续未生成可执行命令，已自动结束。")
                        ui.print_separator()
                        return {
                            "success": False,
                            "steps": steps,
                            "summary": "LLM 返回 None 响应"
                        }
                    continue
                
                if not response.strip():
                    ui.print_error("LLM 返回了空响应（只包含空白字符），请检查模型配置或网络连接")
                    ui.print_info(f"响应内容（repr）: {repr(response)}")
                    idle_steps += 1
                    if idle_steps >= self.max_idle_steps:
                        ui.print_warning("连续未生成可执行命令，已自动结束。")
                        ui.print_separator()
                        return {
                            "success": False,
                            "steps": steps,
                            "summary": "LLM 返回空响应"
                        }
                    continue
                
                self.add_to_history("assistant", response)
                command_info = self._parse_response(response)
                
                if command_info is None:
                    # Parse failed, display response content and error message
                    ui.print_assistant(response)
                    ui.print_warning("无法从响应中解析出有效的 JSON 命令格式。LLM 可能返回了非结构化响应。")
                    ui.print_info("提示：LLM 应该返回 JSON 格式，包含 'command' 或 'status' 字段")
                    
                    if self._is_final_summary(response):
                        ui.print_separator()
                        return {
                            "success": True,
                            "steps": steps,
                            "summary": response
                        }
                    idle_steps += 1
                    if idle_steps >= self.max_idle_steps:
                        ui.print_warning("连续未生成可执行命令，已自动结束。")
                        ui.print_separator()
                        return {
                            "success": False,
                            "steps": steps,
                            "summary": response or "未生成可执行命令"
                        }
                    continue
                else:
                    idle_steps = 0
                
                # Check if it's an interaction request (only in interactive mode, not AUTO mode)
                if "interaction" in command_info and command_info["interaction"]:
                    if not self.auto_mode:  # Only supported in interactive mode
                        interaction_message = command_info.get("message", response)
                        interaction_options = command_info.get("options", None)
                        allow_custom_input = command_info.get("allow_custom_input", False)
                        
                        # First display Agent's interaction message
                        ui.print_assistant(interaction_message)
                        
                        # If there are options, use option selection; otherwise use normal input
                        if interaction_options and len(interaction_options) > 0:
                            # Build option list, format: [(display text, user input value), ...]
                            # After user selection, directly use option text as user input
                            options = []
                            for opt in interaction_options:
                                if isinstance(opt, dict):
                                    # Format: {"text": "display text"}, use text as user input
                                    text = opt.get("text", "")
                                    options.append((text, text))
                                elif isinstance(opt, str):
                                    # Simple string format, display and input are the same string
                                    options.append((opt, opt))
                                else:
                                    opt_str = str(opt)
                                    options.append((opt_str, opt_str))
                            
                            # Use option selection, returned value directly as user input
                            user_response = ui.select_option(
                                "",  # message already displayed above
                                options,
                                default_index=0,
                                allow_custom_input=allow_custom_input
                            )
                            
                            # Display user's selection result
                            ui.print_user(user_response)
                        else:
                            # No options, use normal input
                            user_response = ui.input_prompt("")
                            if user_response:
                                ui.print_user(user_response)
                        
                        if not user_response:
                            # User didn't provide input, end current task
                            ui.print_info("未收到用户输入，任务已结束")
                            return {
                                "success": False,
                                "steps": steps,
                                "summary": "用户取消交互"
                            }
                        
                        # Add user response to history
                        self.add_to_history("user", user_response)
                        # Continue execution, don't return interaction flag
                        continue
                    else:
                        # AUTO mode: ignore interaction requests, continue execution
                        ui.print_warning("AUTO模式下不支持交互，将尝试自动处理")
                        continue
                
                thought = command_info.get("thought")
                if thought:
                    ui.print_thinking(thought=thought)
                
                if "status" in command_info:
                    status = command_info["status"]
                    summary = command_info.get("summary", "")
                    # Display response content
                    if summary:
                        ui.print_assistant(summary)
                    ui.print_separator()
                    return {
                        "success": status == "success",
                        "steps": steps,
                        "summary": summary
                    }
                
                if "command" in command_info:
                    command = command_info["command"]
                    is_valid, msg = self.tool.validate_command(command)
                    if not is_valid:
                        ui.print_error(f"命令验证失败: {msg}")
                        observation = f"命令不合法: {msg}"
                        self.add_to_history("user", observation)
                        idle_steps += 1
                        if idle_steps >= self.max_idle_steps:
                            ui.print_warning("连续无效命令，已自动结束。")
                            ui.print_separator()
                            return {
                                "success": False,
                                "steps": steps,
                                "summary": "多次命令无效，已结束此次任务"
                            }
                        continue
                    idle_steps = 0
                    
                    success, stdout, stderr = self.tool.execute(
                        command, 
                        require_confirm=self.require_confirm and not self.auto_mode
                    )
                    
                    step_info = {
                        "step": current_step,
                        "command": command,
                        "success": success,
                        "stdout": stdout,
                        "stderr": stderr
                    }
                    steps.append(step_info)
                    
                    observation = AGENT_OBSERVATION_TEMPLATE.format(
                        command=command,
                        success=success,
                        output=stdout if success else stderr,
                        error=stderr if not success else ""
                    )
                    self.add_to_history("user", observation)
                
            except Exception as e:
                import traceback
                error_msg = str(e)
                ui.print_error(f"执行出错: {error_msg}")
                
                # If verbose mode, display full stack trace
                if Config.VERBOSE:
                    ui.print_error("详细错误信息:")
                    ui.console.print(traceback.format_exc(), style="dim")
                else:
                    # Even if not verbose mode, show error type
                    error_type = type(e).__name__
                    ui.print_info(f"错误类型: {error_type}")
                
                return {
                    "success": False,
                    "steps": steps,
                    "error": error_msg
                }
        
        ui.print_warning(f"已达到最大步数限制 ({self.max_steps})")
        return {
            "success": False,
            "steps": steps,
            "error": "Max steps reached"
        }
    
    def _parse_response(self, response: str) -> Optional[Dict[str, Any]]:
        """
        Parse LLM response, extract JSON format command information
        
        Returns:
            Dictionary on success, None on failure
        """
        parse_errors = []  # Collect all parse error messages
        
        # First try to extract JSON from code blocks
        json_pattern = r'```json\s*(\{.*?\})\s*```'
        matches = re.findall(json_pattern, response, re.DOTALL)
        
        if matches:
            try:
                data = json.loads(matches[0])
                # Check if it's an interaction request
                if data.get("status") == "interaction":
                    return {
                        "interaction": True,
                        "message": data.get("message", response),
                        "options": data.get("options", None),
                        "allow_custom_input": data.get("allow_custom_input", False)
                    }
                return data
            except json.JSONDecodeError as e:
                parse_errors.append(f"代码块 JSON 解析失败: {str(e)}")
        try:
            # First try to find complete JSON object (from { to matching })
            start_idx = response.find('{')
            if start_idx != -1:
                # Start from first {, try to parse
                brace_count = 0
                in_string = False
                escape_next = False
                
                for i in range(start_idx, len(response)):
                    char = response[i]
                    
                    if escape_next:
                        escape_next = False
                        continue
                    
                    if char == '\\':
                        escape_next = True
                        continue
                    
                    if char == '"' and not escape_next:
                        in_string = not in_string
                        continue
                    
                    if not in_string:
                        if char == '{':
                            brace_count += 1
                        elif char == '}':
                            brace_count -= 1
                            if brace_count == 0:
                                # Found complete JSON object
                                json_str = response[start_idx:i+1]
                                try:
                                    data = json.loads(json_str)
                                    # Check if it's an interaction request
                                    if data.get("status") == "interaction":
                                        return {
                                            "interaction": True,
                                            "message": data.get("message", response),
                                            "options": data.get("options", None),
                                            "allow_custom_input": data.get("allow_custom_input", False)
                                        }
                                    if "command" in data or "status" in data:
                                        return data
                                except json.JSONDecodeError as e:
                                    parse_errors.append(f"文本 JSON 解析失败 (位置 {start_idx}-{i+1}): {str(e)}")
                                break
        except Exception as e:
            parse_errors.append(f"JSON 提取过程出错: {str(e)}")
        
        # If all parsing failed, record error info (but don't print here, let caller decide)
        if parse_errors and Config.VERBOSE:
            # Only record internally in verbose mode, caller will display
            pass
        
        return None
    
    def _is_final_summary(self, response: str) -> bool:
        keywords = ["完成", "完结", "finished", "done", "总结", "summary"]
        response_lower = response.lower()
        return any(kw in response_lower for kw in keywords)

