#!/usr/bin/env python
# coding=utf-8
"""Main entry point - CLI interface"""

import sys
import os
import argparse
from datetime import datetime
from typing import Optional
from .models import LLMFactory
from .agents import CompleteAgent, ChatAgent, CommandAgent
from .tools import TerminalTool
from .ui import ui
from .config import Config


class MiniShellAgent:
    """MiniShellAgent main class"""
    
    def __init__(
        self, 
        llm_type: str = "openai",
        mode: str = "agent",
        agent_mode_type: str = "interactive",
        **kwargs
    ):
        """
        Initialize the agent
        
        Args:
            llm_type: LLM type (openai/llama)
            mode: Working mode (complete/chat/agent)
            agent_mode_type: Agent mode type ("auto" for full auto / "interactive" for interactive)
            **kwargs: Other parameters
        """
        # Ensure working directory is the runtime directory
        # Use environment variable if set by wrapper script, otherwise use current directory
        initial_cwd = os.environ.get('MINISHELLAGENT_RUNTIME_CWD', os.getcwd())
        if os.getcwd() != initial_cwd:
            os.chdir(initial_cwd)
        
        # Load user configuration
        user_config = Config.load_user_config()
        
        self.llm_type = llm_type
        self.mode = mode
        
        # Agent mode type: auto (full auto) or interactive (interactive mode)
        self.agent_mode_type = user_config.get("agent_mode_type", agent_mode_type) if self.mode == "agent" else "interactive"
        # Whether confirmation is required (can be controlled in both AUTO and interactive modes)
        self.require_confirm = kwargs.get("require_confirm", user_config.get("require_confirm", True))
        self.kwargs = kwargs
        # Complete mode uses persistent shell to simulate normal terminal behavior
        self.complete_tool = TerminalTool(use_persistent_shell=(mode == "complete"))
        
        # Create LLM instance
        try:
            ui.print_info(f"Initializing {llm_type.upper()} model...")
            self.llm = LLMFactory.create(llm_type, **kwargs)
            model_name = self.llm.get_model_name()
            ui.print_success(f"模型加载成功: {model_name}")
        except Exception as e:
            ui.print_error(f"模型加载失败: {str(e)}")
            sys.exit(1)
        
        # Create corresponding Agent
        self.agent = self._create_agent(mode)
    
    def _create_agent(self, mode: str):
        """Create Agent"""
        if mode == "complete":
            return CompleteAgent(self.llm)
        elif mode == "chat":
            return ChatAgent(self.llm)
        elif mode == "agent":
            # Determine if confirmation is needed based on agent_mode_type
            # AUTO mode: full auto, no confirmation needed
            # Interactive mode: based on require_confirm
            require_confirm = False if self.agent_mode_type == "auto" else self.require_confirm
            
            return CommandAgent(
                self.llm,
                max_steps=self.kwargs.get("max_steps", Config.MAX_STEPS),
                require_confirm=require_confirm,
                auto_mode=(self.agent_mode_type == "auto"),
                max_idle_steps=self.kwargs.get("max_idle_steps", Config.MAX_IDLE_STEPS),
            )
        else:
            raise ValueError(f"Unknown mode: {mode}")
    
    def _save_user_config(self):
        """Save user configuration"""
        config = {
            "mode": self.mode,
            "agent_mode_type": self.agent_mode_type,
            "require_confirm": self.require_confirm,
        }
        Config.save_user_config(config)
    
    def _process_slash_command(self, user_input: str) -> str:
        """
        Process control commands starting with '/'
        
        Returns:
        - "switch": Mode switched, caller should re-enter new mode loop
        - "handled": Command handled, no need to continue current logic
        - "none": Not a control command
        """
        if not user_input.startswith("/"):
            return "none"
        
        parts = user_input[1:].strip().split(None, 1)  # Split only first space, keep rest
        if not parts:
            ui.print_warning("Please enter a valid control command, e.g., /help")
            return "handled"
        
        cmd = parts[0].lower()
        # Preserve original arguments for some commands (e.g., file paths)
        arg = parts[1] if len(parts) > 1 else None
        
        if cmd == "help":
            help_text = "Available control commands:\n"
            help_text += "  /help - Show help information\n"
            help_text += "  /config - Adjust system configuration\n"
            help_text += "  /confirm [on|off] - Control whether command confirmation is required (available in both AUTO and interactive modes)\n"
            help_text += "  /auto - Switch to AUTO mode (fully automatic execution)\n"
            help_text += "  /interactive - Switch to interactive mode (can initiate interactions)\n"
            help_text += "  /clean - Clear context and start new conversation (also resets token statistics)\n"
            help_text += "  /export [file_path] - Export current conversation history to file\n"
            help_text += "  /chat, /agent, /complete - Switch working mode\n"
            help_text += "  /exit - Exit program"
            ui.print_info(help_text)
            return "handled"
        
        if cmd == "config":
            return self._handle_config_command()
        
        if cmd == "confirm":
            if self.mode != "agent":
                ui.print_warning("This command is only valid in agent mode")
                return "handled"
            if arg and arg.lower() in ("on", "off"):
                self.require_confirm = arg.lower() == "on"
            else:
                self.require_confirm = not self.require_confirm
            ui.print_info(f"Command confirmation {'enabled' if self.require_confirm else 'disabled'}")
            self._save_user_config()
            # Recreate agent to apply new confirmation settings
            self.agent = self._create_agent("agent")
            return "handled"
        
        if cmd == "auto":
            if self.mode != "agent":
                ui.print_warning("This command is only valid in agent mode")
                return "handled"
            self.agent_mode_type = "auto"
            self.require_confirm = False  # AUTO mode defaults to no confirmation
            ui.print_info("Switched to AUTO mode (fully automatic execution, no confirmation required)")
            self._save_user_config()
            self.agent = self._create_agent("agent")
            return "handled"
        
        if cmd == "interactive":
            if self.mode != "agent":
                ui.print_warning("This command is only valid in agent mode")
                return "handled"
            self.agent_mode_type = "interactive"
            # Interactive mode defaults to requiring confirmation, but can be modified via /confirm command
            if not hasattr(self, 'require_confirm') or self.require_confirm is None:
                self.require_confirm = True
            ui.print_info(f"Switched to interactive mode (can control confirmation requirement via /confirm command)")
            self._save_user_config()
            self.agent = self._create_agent("agent")
            return "handled"
        
        if cmd == "clean":
            if hasattr(self, 'agent') and hasattr(self.agent, 'clear_history'):
                self.agent.clear_history()
                # Re-add system prompt
                if hasattr(self.agent, 'system_prompt'):
                    from .prompts import get_agent_system_prompt
                    self.agent.system_prompt = get_agent_system_prompt()  # Update environment info
                    self.agent.add_to_history("system", self.agent.system_prompt)
                # Clear token and call statistics
                if hasattr(self, 'llm') and hasattr(self.llm, 'reset_token_stats'):
                    self.llm.reset_token_stats()
                ui.print_success("上下文已清空，token统计已重置，开始新对话")
            else:
                ui.print_warning("当前模式不支持清空上下文")
            return "handled"
        
        if cmd == "export":
            return self._handle_export_command(arg)
        
        if cmd in ("chat", "agent", "complete"):
            if cmd == self.mode:
                ui.print_info(f"已处于 {cmd} 模式")
                return "handled"
            # Auto clean when switching modes (clear context and token statistics)
            if hasattr(self, 'agent') and hasattr(self.agent, 'clear_history'):
                self.agent.clear_history()
            if hasattr(self, 'llm') and hasattr(self.llm, 'reset_token_stats'):
                self.llm.reset_token_stats()
            
            # Recreate complete_tool with persistent shell when switching to complete mode
            if cmd == "complete":
                # Close old complete_tool if exists
                if hasattr(self, 'complete_tool') and self.complete_tool:
                    self.complete_tool.close()
                # Create new persistent shell
                self.complete_tool = TerminalTool(use_persistent_shell=True)
            
            self.mode = cmd
            self._save_user_config()
            self.agent = self._create_agent(cmd)
            ui.print_info(f"已切换至 {cmd} 模式（已清空上下文和token统计）")
            return "switch"
        
        if cmd == "exit":
            self._save_user_config()
            ui.print_info("已退出")
            return "exit"
        
        ui.print_warning(f"Unknown control command: /{cmd}")
        return "handled"
    
    def _handle_config_command(self) -> str:
        """
        Handle /config command, provide interactive configuration interface
        
        Returns: "handled" or "switch" (if mode was switched)
        """
        ui.print_info("=== 系统配置 ===")
        
        # Display current configuration
        ui.print_info(f"当前工作模式: {self.mode}")
        if self.mode == "agent":
            ui.print_info(f"Agent模式类型: {self.agent_mode_type}")
            ui.print_info(f"需要确认: {'是' if self.require_confirm else '否'}")
        ui.print_info(f"LLM类型: {self.llm_type}")
        
        # Configuration options
        config_options = [
            ("工作模式 (mode)", [
                ("complete - 补全模式", "complete"),
                ("chat - 对话模式", "chat"),
                ("agent - Agent模式", "agent"),
            ]),
        ]
        
        # If in agent mode, add agent-related configuration
        if self.mode == "agent":
            config_options.append(("Agent模式类型 (agent_mode_type)", [
                ("auto - 全自动执行", "auto"),
                ("interactive - 交互模式", "interactive"),
            ]))
            config_options.append(("命令确认 (require_confirm)", [
                ("是 - 需要确认", True),
                ("否 - 无需确认", False),
            ]))
        
        # Let user select configuration item to modify
        config_option_list = [(opt[0], i) for i, opt in enumerate(config_options)]
        config_option_list.append(("取消", -1))
        
        try:
            selected_option_idx = ui.select_option(
                "请选择要修改的配置项",
                config_option_list,
                default_index=0
            )
            
            if selected_option_idx == -1:
                ui.print_info("已取消配置")
                return "handled"
            
            if selected_option_idx < 0 or selected_option_idx >= len(config_options):
                ui.print_warning("无效的选项")
                return "handled"
            
            config_name, config_values = config_options[selected_option_idx]
            
            # Display current value and determine default option
            default_idx = 0
            if config_name.startswith("工作模式"):
                current_value = self.mode
                # Find index of current value in options
                for i, (_, val) in enumerate(config_values):
                    if val == current_value:
                        default_idx = i
                        break
            elif config_name.startswith("Agent模式类型"):
                current_value = self.agent_mode_type
                for i, (_, val) in enumerate(config_values):
                    if val == current_value:
                        default_idx = i
                        break
            elif config_name.startswith("命令确认"):
                current_value = self.require_confirm
                for i, (_, val) in enumerate(config_values):
                    if val == current_value:
                        default_idx = i
                        break
            else:
                current_value = None
            
            ui.print_info(f"\n当前 {config_name}: {current_value}")
            
            # Use select_option to let user choose new value
            selected = ui.select_option(
                f"选择 {config_name}",
                config_values,
                default_index=default_idx
            )
            
            # Apply configuration
            need_switch = False
            if config_name.startswith("工作模式"):
                if selected != self.mode:
                    self.mode = selected
                    need_switch = True
                    ui.print_success(f"工作模式已设置为: {selected}")
            elif config_name.startswith("Agent模式类型"):
                if selected != self.agent_mode_type:
                    self.agent_mode_type = selected
                    if selected == "auto":
                        self.require_confirm = False
                    ui.print_success(f"Agent模式类型已设置为: {selected}")
            elif config_name.startswith("命令确认"):
                if selected != self.require_confirm:
                    self.require_confirm = selected
                    ui.print_success(f"命令确认已{'开启' if selected else '关闭'}")
            
            self._save_user_config()
            
            # If mode was switched, need to recreate agent
            if need_switch:
                self.agent = self._create_agent(self.mode)
                return "switch"
            elif self.mode == "agent":
                # Even if mode wasn't switched, if agent-related config was modified, need to recreate
                self.agent = self._create_agent("agent")
            
            return "handled"
            
        except (ValueError, KeyboardInterrupt, EOFError):
            ui.print_info("已取消配置")
            return "handled"
    
    def _handle_export_command(self, file_path: Optional[str] = None) -> str:
        """
        Handle /export command, export current conversation history to file
        
        Returns: "handled"
        """
        if not hasattr(self, 'agent') or not hasattr(self.agent, 'history'):
            ui.print_warning("当前模式不支持导出对话历史")
            return "handled"
        
        history = self.agent.history
        if not history:
            ui.print_warning("当前没有对话历史可导出")
            return "handled"
        
        # If no file path specified, use default path
        if not file_path:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_path = f"minishellagent_history_{timestamp}.txt"
        
        # Ensure file path is absolute or relative to current working directory
        if not os.path.isabs(file_path):
            file_path = os.path.join(os.getcwd(), file_path)
        
        try:
            # Format conversation history
            export_lines = []
            export_lines.append("=" * 80)
            export_lines.append(f"MiniShellAgent 对话历史导出")
            export_lines.append(f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            export_lines.append(f"模型: {self.llm.get_model_name()}")
            stats = self.llm.get_token_stats()
            if stats.get("call_count", 0) > 0:
                export_lines.append(f"Token统计: 调用 {stats['call_count']}次 | "
                                   f"输入 {stats['prompt_tokens']:,} tokens | "
                                   f"输出 {stats['completion_tokens']:,} tokens | "
                                   f"总计 {stats['total_tokens']:,} tokens")
            export_lines.append("=" * 80)
            export_lines.append("")
            
            # Export conversation content
            for msg in history:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                
                if role == "system":
                    export_lines.append(f"[系统提示]")
                    export_lines.append("-" * 80)
                    export_lines.append(content)
                    export_lines.append("")
                elif role == "user":
                    export_lines.append(f"[用户]")
                    export_lines.append("-" * 80)
                    export_lines.append(content)
                    export_lines.append("")
                elif role == "assistant":
                    export_lines.append(f"[助手]")
                    export_lines.append("-" * 80)
                    export_lines.append(content)
                    export_lines.append("")
                else:
                    export_lines.append(f"[{role}]")
                    export_lines.append("-" * 80)
                    export_lines.append(content)
                    export_lines.append("")
            
            # Write to file
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(export_lines))
            
            ui.print_success(f"对话历史已导出到: {file_path}")
            ui.print_info(f"共导出 {len(history)} 条消息")
            
        except Exception as e:
            ui.print_error(f"导出失败: {str(e)}")
            if Config.VERBOSE:
                import traceback
                ui.print_error("详细错误信息:")
                ui.console.print(traceback.format_exc(), style="dim")
        
        return "handled"
    
    def run_complete_mode(self):
        """Run complete mode"""
        ui.print_mode_info("complete")
        ui.print_info("输入 /help 查看帮助")
        
        # Ensure agent is CompleteAgent type
        if not isinstance(self.agent, CompleteAgent):
            self.agent = self._create_agent("complete")
        
        while True:
            try:
                # Completions generated by LLM; history only for LLM reference
                def llm_fetcher(prefix: str):
                    return self.agent.complete(prefix, silent=True)
                
                # Generate shell prompt
                shell_prompt = ui.get_shell_prompt()
                user_input = ui.input_prompt(
                    shell_prompt,
                    history_suggestions=[],  # Don't use history directly as candidates
                    llm_fetcher=llm_fetcher
                )
                
                if not user_input:
                    continue
                
                slash_result = self._process_slash_command(user_input)
                if slash_result == "switch":
                    return "switch"
                if slash_result == "exit":
                    return "exit"
                if slash_result == "handled":
                    continue
                
                # Execute command on Enter (no confirmation, but keep safety checks)
                is_valid, msg = self.complete_tool.validate_command(user_input)
                if not is_valid:
                    ui.print_error(f"命令验证失败: {msg}")
                    continue
                
                self.complete_tool.execute(
                    user_input,
                    require_confirm=False,  # Direct execution in complete mode
                    timeout=30,
                    show_status=False  # Don't show command status in complete mode, like native terminal
                )
                
            except KeyboardInterrupt:
                print()
                ui.print_info("已退出")
                return "exit"
            except Exception as e:
                ui.print_error(f"发生错误: {str(e)}")
    
    def run_chat_mode(self):
        """Run chat mode"""
        ui.print_mode_info("chat")
        ui.print_info("您可以问我任何关于命令行的问题")
        ui.print_info("输入 /help 查看帮助")
        
        while True:
            try:
                user_input = ui.input_prompt("请输入 (输入 '/exit' 退出, 'clear' 清空历史): ")
                
                if not user_input:
                    continue
                
                slash_result = self._process_slash_command(user_input)
                if slash_result == "switch":
                    return "switch"
                if slash_result == "exit":
                    return "exit"
                if slash_result == "handled":
                    continue
                
                if user_input.lower() in ['clear', '清空']:
                    self.agent.clear_history()
                    # Re-add system prompt
                    if hasattr(self.agent, 'system_prompt'):
                        self.agent.add_to_history("system", self.agent.system_prompt)
                    ui.print_success("对话历史已清空")
                    continue
                
                # Chat
                ui.print_user(user_input)
                response = self.agent.chat(user_input)
                ui.print_assistant(response)
                ui.print_separator()
                
            except KeyboardInterrupt:
                print()
                ui.print_info("已退出")
                return "exit"
            except Exception as e:
                ui.print_error(f"发生错误: {str(e)}")
    
    def run_agent_mode(self, task: Optional[str] = None):
        """Run agent mode"""
        ui.print_mode_info("agent")
        
        if self.agent_mode_type == "auto":
            ui.print_info("AUTO模式 - 全自动执行，无需确认")
        else:
            confirm_status = "需要确认" if self.require_confirm else "无需确认"
            ui.print_info(f"交互模式 - {confirm_status}（使用 /confirm 命令切换）")
        
        # Show help hint when starting interactive mode
        if not task:
            ui.print_info("输入 /help 查看帮助，直接输入任务描述开始使用")
        
        if task:
            # Single task mode
            result = self.agent.run(task)
            
            if result.get("success"):
                ui.print_success("任务执行成功")
            else:
                ui.print_error(f"任务执行失败: {result.get('error', 'Unknown error')}")
            
            return result
        
        # Interactive mode - continuous conversation like Claude Code
        while True:
            try:
                task = ui.input_prompt("")
                
                if not task:
                    continue
                
                slash_result = self._process_slash_command(task)
                if slash_result == "switch":
                    return "switch"
                if slash_result == "exit":
                    return "exit"
                if slash_result == "handled":
                    continue
                
                # Execute task
                result = self.agent.run(task)
                
                # Interaction requests are now handled inside agent.run(), no extra handling needed here
                # After task completion, directly wait for next input without asking if continue
                # This is more fluid, similar to Claude Code's interaction style
                
            except KeyboardInterrupt:
                print()
                ui.print_info("已退出")
                return "exit"
            except Exception as e:
                ui.print_error(f"发生错误: {str(e)}")
                # Continue waiting for input after error, don't exit
    
    def run(self, task: Optional[str] = None):
        """Run the agent"""
        # Single task mode: execute directly if task provided
        if task and self.mode == "agent":
            return self.run_agent_mode(task)
        
        while True:
            if self.mode == "complete":
                signal = self.run_complete_mode()
            elif self.mode == "chat":
                signal = self.run_chat_mode()
            elif self.mode == "agent":
                signal = self.run_agent_mode()
            else:
                ui.print_error(f"Unknown mode: {self.mode}")
                sys.exit(1)
            
            if signal == "switch":
                # Mode switched, re-enter new mode loop
                continue
            # Includes exit or None, exit main loop
            break


def main():
    """CLI entry point"""
    # Get the real runtime working directory from environment variable
    # If wrapper script is used, it will set MINISHELLAGENT_RUNTIME_CWD
    runtime_cwd = os.environ.get('MINISHELLAGENT_RUNTIME_CWD', os.getcwd())
    
    # Ensure we're in the runtime directory
    if os.getcwd() != runtime_cwd:
        os.chdir(runtime_cwd)
    
    # Load user configuration to get default mode
    user_config = Config.load_user_config()
    default_mode = user_config.get("mode", "agent")
    default_agent_mode_type = user_config.get("agent_mode_type", "interactive")
    default_require_confirm = user_config.get("require_confirm", True)
    
    parser = argparse.ArgumentParser(
        description="北京邮电大学操作系统 Homework 1: Command Line Tool Helper using LLMs\n"
                    "Author: Haotian Ren - Artificial Intelligence",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # Chat mode (default uses OpenAI)
  %(prog)s --mode chat
  
  # Run a single task with Agent mode
  %(prog)s --mode agent --task "查找当前目录下最大的5个文件"
  
  # Completion mode
  %(prog)s --mode complete
  
  # Use a local Llama model
  %(prog)s --llm llama --mode agent
  
  # Override the OpenAI base_url
  %(prog)s --base-url https://api.example.com/v1
        """
    )
    
    # Mode selection
    parser.add_argument(
        "--mode", "-m",
        choices=["complete", "chat", "agent"],
        default=default_mode,
        help=f"工作模式: complete(命令补全) / chat(对话) / agent(智能执行) [默认: {default_mode}]"
    )
    
    # LLM selection
    parser.add_argument(
        "--llm", "-l",
        choices=["openai", "llama"],
        default="openai",
        help="LLM类型: openai / llama"
    )
    
    # OpenAI configuration
    parser.add_argument(
        "--api-key",
        help="OpenAI API Key (或通过环境变量 OPENAI_API_KEY 设置)"
    )
    
    parser.add_argument(
        "--base-url",
        help="OpenAI API Base URL (或通过环境变量 OPENAI_BASE_URL 设置)"
    )
    
    parser.add_argument(
        "--model",
        help="模型名称 (或通过环境变量 OPENAI_MODEL 设置)"
    )
    
    # Llama configuration
    parser.add_argument(
        "--model-path",
        help="Llama模型路径 (或通过环境变量 LLAMA_MODEL_PATH 设置)"
    )
    
    # Agent configuration
    parser.add_argument(
        "--task", "-t",
        help="要执行的任务（仅在agent模式下有效）"
    )
    
    parser.add_argument(
        "--max-steps",
        type=int,
        default=Config.MAX_STEPS,
        help=f"最大执行步数 (默认: {Config.MAX_STEPS})"
    )
    
    parser.add_argument(
        "--agent-mode-type",
        choices=["auto", "interactive"],
        default=default_agent_mode_type,
        help=f"Agent模式类型: auto(全自动执行) / interactive(交互模式) [默认: {default_agent_mode_type}]"
    )
    
    
    parser.add_argument(
        "--no-safe-mode",
        action="store_true",
        help="禁用安全模式（允许执行危险命令）"
    )
    
    # UI configuration
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="禁用彩色输出"
    )
    
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="详细输出"
    )
    
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0"
    )
    
    args = parser.parse_args()
    
    # Update configuration
    if args.no_color:
        Config.ENABLE_COLORS = False
    if args.verbose:
        Config.VERBOSE = True
    if args.no_safe_mode:
        Config.SAFE_MODE = False
    
    # Print welcome message
    ui.print_banner()
    
    # Prepare LLM configuration
    llm_kwargs = {}
    if args.llm == "openai":
        if args.api_key:
            llm_kwargs["api_key"] = args.api_key
        if args.base_url:
            llm_kwargs["base_url"] = args.base_url
        if args.model:
            llm_kwargs["model"] = args.model
    elif args.llm == "llama":
        if args.model_path:
            llm_kwargs["model_path"] = args.model_path
    
    # Prepare Agent configuration
    agent_kwargs = {
        **llm_kwargs,
        "max_steps": args.max_steps,
    }
    
    # Create and run agent
    helper = None
    try:
        helper = MiniShellAgent(
            llm_type=args.llm,
            mode=args.mode,
            agent_mode_type=args.agent_mode_type,
            **agent_kwargs
        )
        helper.run(task=args.task)
    except KeyboardInterrupt:
        print()
        ui.print_info("程序被用户中断")
    except Exception as e:
        ui.print_error(f"程序异常退出: {str(e)}")
        if args.verbose:
            import traceback
            traceback.print_exc()
    finally:
        # Save user configuration
        if helper:
            helper._save_user_config()
        # Cleanup resources
        if helper and hasattr(helper, 'complete_tool'):
            helper.complete_tool.close()
    sys.exit(0)


if __name__ == "__main__":
    main()

