#!/usr/bin/env python
# coding=utf-8

import os
import sys
import platform
import getpass
import socket
import subprocess
import shlex
from pathlib import Path
from typing import Optional
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text
from rich.theme import Theme
from rich.prompt import Confirm
from .config import Config

# Optional prompt_toolkit dependency for closer VSCode-style inline completions
try:
    from prompt_toolkit import prompt as pt_prompt, Application
    from prompt_toolkit.completion import WordCompleter, Completion
    from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
    from prompt_toolkit.formatted_text import ANSI, FormattedText
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout, Container, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.containers import HSplit
    from prompt_toolkit.shortcuts import PromptSession
    _HAS_PT = True
except Exception:
    _HAS_PT = False

claude_theme = Theme({
    "info": "cyan",
    "warning": "yellow",
    "error": "bold red",
    "success": "bold green",
    "command": "bold white on #2b2b2b",
    "assistant": "bold #39CCCC",
    "user": "bold #2ECC40",
    "thinking": "dim italic white",
    "banner.title": "bold cyan",
    "banner.text": "bright_white",
    "step": "bold magenta",
})


class UI:
    def __init__(self, enable_colors: bool = True):
        self.console = Console(theme=claude_theme, force_terminal=enable_colors and Config.ENABLE_COLORS)
    
    def print_banner(self):
        title = Text("MiniShellAgent v0.1.0", style="banner.title")
        # Create multi-line content with professional layout
        banner_lines = [
            "北京邮电大学 操作系统",
            "Homework 1: Command Line Tool Helper using LLMs",
            "",
            "Author: Haotian Ren",
            "人工智能专业"
        ]
        content = Text("\n".join(banner_lines), style="banner.text", justify="center")
        panel = Panel(
            content,
            title=title,
            border_style="cyan",
            padding=(1, 2),
            width=70,
        )
        self.console.print(panel)
    
    def print_mode_info(self, mode: str):
        mode_info = {
            "complete": "补全模式 - 自动提示可能的命令",
            "chat": "对话模式 - 与AI助手交流",
            "agent": "Agent模式 - 智能执行命令",
        }
        info = mode_info.get(mode, mode)
        self.console.print(f"\n[info]当前模式:[/info] [bold blue]{info}[/bold blue]\n")
    
    def print_user(self, message: str):
        self.console.print(f"[user]You:[/user] {message}")
    
    def print_assistant(self, message: str):
        self.console.print(f"[assistant]Assistant:[/assistant] {message}")
    
    def print_thinking(self, message: str = "思考中...", thought: str = None):
        if thought:
            panel = Panel(
                f"[thinking]{thought}[/thinking]",
                title="[dim]思考过程[/dim]",
                border_style="dim",
                padding=(0, 1),
                title_align="left"
            )
            self.console.print(panel)
        elif message != "思考中...":
            self.console.print(f"[thinking]思考: {message}[/thinking]")
    
    def print_command(self, command: str, status: str = "pending"):
        if status == "pending":
            self.console.print(f"[dim]▸[/dim] [bold white]{command}[/bold white]")
        elif status == "executing":
            pass
        elif status == "success":
            self.console.print(f"[green]✓[/green] [dim]{command}[/dim]")
        elif status == "error":
            self.console.print(f"[red]✗[/red] [dim]{command}[/dim]")
        else:
            self.console.print(f"[dim]▸[/dim] [bold white]{command}[/bold white]")
    
    def print_output(self, output: str, is_error: bool = False):
        if not output or not output.strip():
            return
        
        if len(output.strip()) < 100:
            if is_error:
                self.console.print(f"[red]{output.strip()}[/red]")
            else:
                self.console.print(f"[dim]{output.strip()}[/dim]")
        else:
            if is_error:
                panel = Panel(output.strip(), title="Error", border_style="red", title_align="left", padding=(0, 1))
            else:
                panel = Panel(output.strip(), title="Output", border_style="dim white", title_align="left", padding=(0, 1))
            self.console.print(panel)
    
    def print_warning(self, message: str):
        self.console.print(f"[warning]Warning:[/warning] {message}")
    
    def print_error(self, message: str):
        self.console.print(f"[error]Error:[/error] {message}")
    
    def print_success(self, message: str):
        self.console.print(f"[success]Success:[/success] {message}")
    
    def print_info(self, message: str):
        self.console.print(f"[info]Info:[/info] {message}")
    
    def print_token_stats(self, model_name: str, stats: dict):
        """Display token statistics"""
        if stats.get("call_count", 0) == 0:
            return
        
        prompt_tokens = stats.get("prompt_tokens", 0)
        completion_tokens = stats.get("completion_tokens", 0)
        total_tokens = stats.get("total_tokens", 0)
        call_count = stats.get("call_count", 0)
        
        stats_text = (
            f"[dim]模型: {model_name} | "
            f"调用: {call_count}次 | "
            f"输入: {prompt_tokens:,} tokens | "
            f"输出: {completion_tokens:,} tokens | "
            f"总计: {total_tokens:,} tokens[/dim]"
        )
        self.console.print(stats_text)
    
    def print_step(self, step_num: int, total_steps: int, description: str):
        self.console.print()
        self.console.rule(f"[step]Step {step_num}/{total_steps}[/step] {description}", style="dim")
        self.console.print()
    
    def print_separator(self, char: str = "─", length: int = 60):
        self.console.rule(style="dim")
    
    def confirm(self, message: str, default: bool = True) -> bool:
        """Confirmation dialog, use options instead of y/n input"""
        options = [
            ("是", True),
            ("否", False)
        ]
        if default:
            selected = self.select_option(message, options, default_index=0)
        else:
            selected = self.select_option(message, options, default_index=1)
        return selected
    
    def select_option(
        self, 
        message: str, 
        options: list, 
        default_index: int = 0,
        allow_custom_input: bool = False
    ) -> str:
        """
        Option selection method, supports up/down arrow selection
        
        Args:
            message: Prompt message
            options: Option list, format: [(display text, user input value), ...], max 4 options
            default_index: Default selected index
            allow_custom_input: Whether the last option allows custom input (only valid when number of options >= 2)
        
        Returns:
            Selected option's corresponding user input value (added to conversation history as user input),
            if custom input is allowed and the last option is selected, return user's direct input
        """
        if len(options) > 4:
            options = options[:4]
        
        allow_input = allow_custom_input and len(options) >= 2
        current_index = [default_index]  # Use list to allow modification in closure
        selected_value = [None]  # Store selected value
        
        def render_options():
            """Render option list to console"""
            if message:
                self.console.print(f"[bold cyan]┌─[/bold cyan] {message}")
            else:
                self.console.print("[bold cyan]┌─[/bold cyan]")
            
            for i, (text, value) in enumerate(options):
                if i == current_index[0]:
                    # Selected option
                    prefix = "[bold cyan]│  ▶[/bold cyan]"
                    text_style = "[bold white]"
                    end_style = "[/bold white]"
                else:
                    # Unselected option
                    prefix = "[dim]│    [/dim]"
                    text_style = "[dim]"
                    end_style = "[/dim]"
                
                if allow_input and i == len(options) - 1:
                    hint = " [dim](可自定义输入)[/dim]"
                else:
                    hint = ""
                
                self.console.print(f"{prefix} {text_style}{text}{end_style}{hint}")
            
            # Footer border and hint
            hint_text = "[dim]使用 ↑↓ 选择，回车确认[/dim]"
            if allow_input and current_index[0] == len(options) - 1:
                hint_text = "[dim]使用 ↑↓ 选择，回车确认，或直接输入[/dim]"
            self.console.print(f"[dim]└─[/dim] {hint_text}")
        
        def get_formatted_text():
            """Generate formatted option text for prompt_toolkit."""
            fragments = []
            # Option entries
            for i, (text, value) in enumerate(options):
                if i == current_index[0]:
                    # Selected entry
                    fragments.append(("bold #00ffff", "│  ▶ "))
                    fragments.append(("bold #ffffff", text))
                else:
                    # Unselected entry (dimmed)
                    fragments.append(("#888888", "│    "))
                    fragments.append(("#888888", text))
                
                if allow_input and i == len(options) - 1:
                    fragments.append(("#888888 italic", " (可自定义输入)"))
                
                fragments.append(("", "\n"))
            
            # Footer hint
            fragments.append(("#888888", "└─ "))
            fragments.append(("#888888 italic", "使用 ↑↓ 选择，回车确认"))
            if allow_input and current_index[0] == len(options) - 1:
                fragments.append(("#888888 italic", "，或直接输入"))
            
            return FormattedText(fragments)
        
        # Show the option list statically first
        render_options()
        
        if not _HAS_PT:
            # Fall back to basic input selection
            choice = input("请选择 (输入序号): ").strip()
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(options):
                    return options[idx][1]
            return options[default_index][1]
        
        # Use prompt_toolkit for proper keyboard interaction
        # Update the display via a simple loop
        import sys
        import tty
        import termios
        
        def get_key():
            """Get single keypress"""
            if not sys.stdin.isatty():
                return None
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setraw(sys.stdin.fileno())
                ch = sys.stdin.read(1)
                # Handle special keys
                if ch == '\x1b':  # ESC
                    ch = sys.stdin.read(2)
                    if ch == '[A':  # Up arrow
                        return 'up'
                    elif ch == '[B':  # Down arrow
                        return 'down'
                elif ch == '\r' or ch == '\n':  # Enter
                    return 'enter'
                elif ch == '\x03':  # Ctrl+C
                    raise KeyboardInterrupt
                else:
                    return ch
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        
        try:
            while True:
                key = get_key()
                
                if key == 'up':
                    if current_index[0] > 0:
                        current_index[0] -= 1
                        # Clear and re-render
                        lines_to_clear = len(options) + 2
                        sys.stdout.write(f"\033[{lines_to_clear}A")  # Move up
                        sys.stdout.write("\033[J")  # Clear to end of line
                        render_options()
                        sys.stdout.flush()
                
                elif key == 'down':
                    if current_index[0] < len(options) - 1:
                        current_index[0] += 1
                        # Clear and re-render
                        lines_to_clear = len(options) + 2
                        sys.stdout.write(f"\033[{lines_to_clear}A")
                        sys.stdout.write("\033[J")
                        render_options()
                        sys.stdout.flush()
                
                elif key == 'enter':
                    # If last option is selected and custom input is allowed
                    if allow_input and current_index[0] == len(options) - 1:
                        # New line, then prompt for input
                        self.console.print()
                        user_input = pt_prompt("请输入内容: ")
                        if user_input and user_input.strip():
                            return user_input.strip()
                        return options[len(options) - 1][1]
                    else:
                        # Selection complete, new line (keep option display)
                        self.console.print()
                        return options[current_index[0]][1]
                
                elif key and key.isdigit():
                    # Directly input number to select
                    idx = int(key) - 1
                    if 0 <= idx < len(options):
                        self.console.print()
                        return options[idx][1]
        except (KeyboardInterrupt, EOFError):
            self.console.print()
            return options[default_index][1]
    
    def _get_shell_cwd(self) -> str:
        """
        Get current working directory from shell (real-time)
        
        Returns:
            Current working directory path
        """
        try:
            # Execute pwd command to get shell's current working directory
            result = subprocess.run(
                'pwd',
                shell=True,
                capture_output=True,
                text=True,
                timeout=1
            )
            if result.returncode == 0:
                cwd = result.stdout.strip()
                if os.path.isdir(cwd):
                    # Synchronously update Python process's working directory
                    try:
                        os.chdir(cwd)
                    except Exception:
                        pass
                    return cwd
        except Exception:
            pass
        # Fall back to Python process's working directory
        return os.getcwd()
    
    def get_shell_prompt(self) -> str:
        """
        Get real terminal prompt.
        Prioritize getting real PS1 prompt from shell, fall back to simulation if failed.
        Get current working directory from shell in real-time on each call.
        
        Returns:
            Shell prompt string
        """
        if platform.system() == "Windows":
            # Windows uses simplified prompt
            shell_cwd = self._get_shell_cwd()
            try:
                username = getpass.getuser()
            except Exception:
                username = os.environ.get('USERNAME', 'user')
            return f"{username}@{socket.gethostname()} {shell_cwd}> "
        
        shell = os.environ.get('SHELL', '/bin/zsh')
        
        # First get shell's current working directory (real-time)
        shell_cwd = self._get_shell_cwd()
        
        # Method 1: Try to let shell directly parse and output prompt (most accurate)
        try:
            # zsh: Use print -P to parse PS1, execute in shell's current working directory
            if 'zsh' in shell:
                result = subprocess.run(
                    [shell, '-c', f'cd "{shell_cwd}" && print -P "$PS1"'],
                    capture_output=True,
                    text=True,
                    timeout=1,
                    env=os.environ.copy()
                )
            # bash: Use eval to parse PS1
            elif 'bash' in shell:
                result = subprocess.run(
                    [shell, '-c', f'cd "{shell_cwd}" && eval "echo \\"$PS1\\""'],
                    capture_output=True,
                    text=True,
                    timeout=1,
                    env=os.environ.copy()
                )
            else:
                result = None
            
            if result and result.returncode == 0 and result.stdout.strip():
                prompt = result.stdout.strip()
                # Clean up possible newlines
                prompt = prompt.replace('\n', '').replace('\r', '')
                # Ensure ends with space (if no prompt character)
                if prompt and not prompt.endswith((' ', '%', '#', '$', '>')):
                    prompt += ' '
                if prompt:
                    return prompt
        except Exception:
            # If failed, continue trying other methods
            pass
        
        # Method 2: Read PS1 and manually parse (using real-time working directory)
        try:
            ps1 = os.environ.get('PS1')
            if not ps1:
                # Try to get PS1 definition from shell
                result = subprocess.run(
                    [shell, '-c', 'echo "$PS1"'],
                    capture_output=True,
                    text=True,
                    timeout=1,
                    env=os.environ.copy()
                )
                if result.returncode == 0:
                    ps1 = result.stdout.strip()
            
            if ps1:
                prompt = self._parse_zsh_prompt(ps1, shell_cwd)
                if prompt:
                    return prompt
        except Exception:
            pass
        
        # Method 3: Fall back to simulation (using real-time working directory)
        return self._generate_simulated_prompt(shell_cwd)
    
    def _parse_zsh_prompt(self, ps1: str, cwd: Optional[str] = None) -> str:
        """
        Parse escape sequences in zsh PS1 prompt
        
        zsh escape sequence examples:
        %n - username
        %m - hostname (short)
        %M - hostname (full)
        %~ - current directory (~ means home)
        %1~ - last part of current directory
        %# - normal user shows %, root shows #
        %(?.%#.%?) - display based on exit status
        %F{color} - foreground color
        %f - reset color
        %B - bold
        %b - reset bold
        
        Args:
            ps1: PS1 prompt string
            cwd: Current working directory (if None, use os.getcwd())
        
        Returns:
            Parsed prompt string, returns None if parsing fails
        """
        try:
            import re
            
            # Get current values
            username = getpass.getuser()
            hostname = socket.gethostname().split('.')[0]  # Short hostname
            try:
                if cwd is None:
                    cwd = os.getcwd()
                home = os.path.expanduser('~')
                if cwd.startswith(home):
                    dirname = '~' + cwd[len(home):]
                else:
                    dirname = cwd
                # If too long, only show last part
                if len(dirname) > 30:
                    dirname = Path(cwd).name
            except Exception:
                dirname = '~'
            
            # Replace escape sequences
            prompt = ps1
            
            # Remove color codes (simplified handling)
            prompt = re.sub(r'%F\{[^}]+\}', '', prompt)
            prompt = re.sub(r'%f', '', prompt)
            prompt = re.sub(r'%B', '', prompt)
            prompt = re.sub(r'%b', '', prompt)
            prompt = re.sub(r'%K\{[^}]+\}', '', prompt)  # Background color
            prompt = re.sub(r'%k', '', prompt)
            
            # Replace basic escape sequences
            prompt = prompt.replace('%n', username)
            prompt = prompt.replace('%m', hostname)
            prompt = prompt.replace('%M', socket.gethostname())
            prompt = prompt.replace('%~', dirname)
            # Use provided cwd or current working directory
            current_cwd = cwd if cwd else os.getcwd()
            prompt = prompt.replace('%1~', Path(current_cwd).name if current_cwd != os.path.expanduser('~') else '~')
            prompt = prompt.replace('%#', '%')  # Normal user shows %
            prompt = prompt.replace('%?', '0')  # Assume exit status is 0
            
            # Handle conditional expression %(?.true.false)
            prompt = re.sub(r'%\(\?\.([^.]*)\.([^)]*)\)', r'\1', prompt)  # Assume condition is true
            
            # Clean up extra whitespace
            prompt = re.sub(r'\s+', ' ', prompt).strip()
            
            # Ensure ends with space (if no prompt character)
            if not prompt.endswith((' ', '%', '#', '$', '>')):
                prompt += ' '
            
            return prompt
        except Exception:
            return None
    
    def _generate_simulated_prompt(self, cwd: Optional[str] = None) -> str:
        """
        Generate simulated shell prompt (fallback)
        
        Args:
            cwd: Current working directory (if None, use os.getcwd())
        
        Returns:
            Simulated shell prompt string
        """
        parts = []
        
        # Get virtual environment name
        venv_name = None
        # Check conda environment (including base)
        conda_env = os.environ.get('CONDA_DEFAULT_ENV')
        if conda_env:
            venv_name = conda_env
        # Check VIRTUAL_ENV (if conda environment doesn't exist)
        if not venv_name:
            virtual_env = os.environ.get('VIRTUAL_ENV')
            if virtual_env:
                venv_name = Path(virtual_env).name
        
        if venv_name:
            parts.append(f"({venv_name})")
        
        # Get username
        try:
            username = getpass.getuser()
        except Exception:
            username = os.environ.get('USER', os.environ.get('USERNAME', 'user'))
        
        # Get hostname
        try:
            hostname = socket.gethostname().split('.')[0]  # Short hostname
        except Exception:
            hostname = 'localhost'
        
        # Get current directory name (use provided cwd or get in real-time)
        try:
            if cwd is None:
                cwd = os.getcwd()
            home = os.path.expanduser('~')
            if cwd.startswith(home):
                dirname = '~' + cwd[len(home):]
            else:
                dirname = cwd
            # If too long, only show last part
            if len(dirname) > 30:
                dirname = Path(cwd).name
        except Exception:
            dirname = '~'
        
        # Combine prompt
        if parts:
            prompt = f"{' '.join(parts)} {username}@{hostname} {dirname} % "
        else:
            prompt = f"{username}@{hostname} {dirname} % "
        
        return prompt
    
    def input_prompt(self, prompt: str = "", history_suggestions=None, llm_fetcher=None) -> str:
        """
        Only provide inline gray hint + Tab accept when prompt_toolkit is available, candidates provided by llm_fetcher.
        If prompt_toolkit is missing, directly prompt missing dependency (no fallback).
        Support system command auto-completion (show available commands when inputting '/').
        """
        if not _HAS_PT:
            self.console.print("[warning]缺少 prompt_toolkit，请安装: pip install prompt_toolkit[/warning]")
            return ""
        
        # System command list (for auto-completion)
        system_commands = [
            "/help", "/confirm", "/auto", "/interactive", "/clean",
            "/chat", "/agent", "/complete", "/exit", "/config", "/export"
        ]
        
        # Candidates provided by LLM, history_suggestions only as additional prefix candidates (can be empty)
        base_items = history_suggestions or []
        
        class HybridCompleter(WordCompleter):
            def __init__(self, base_items, fetcher, system_cmds):
                super().__init__(base_items, ignore_case=True, match_middle=False)
                self.fetcher = fetcher
                self.cache = {}
                self.system_commands = system_cmds
            
            def get_completions(self, document, complete_event):
                text = document.text_before_cursor
                if not text:
                    return []
                
                # If input starts with '/', add system command candidates
                candidates = []
                if text.startswith("/"):
                    # System command completion (auto-display when typing or when pressing Tab)
                    for cmd in self.system_commands:
                        if cmd.startswith(text):
                            candidates.append(cmd)
                else:
                    # Normal command completion
                    candidates = [c for c in base_items if c.startswith(text)]
                    
                    # LLM-provided candidates (only triggered when pressing Tab, not auto-triggered when typing)
                    # When complete_while_typing=True, completion_requested=False when typing characters
                    # When pressing Tab, completion_requested=True
                    if self.fetcher and complete_event.completion_requested:
                        # Only call LLM when there is input content and Tab is pressed
                        if text.strip():
                            if text not in self.cache:
                                try:
                                    result = self.fetcher(text) or []
                                    self.cache[text] = result
                                except Exception as e:
                                    # When completion fails, record error but don't interrupt completion flow
                                    # Printing during completion will interfere with prompt_toolkit's display
                                    # Error message will be displayed in CompleteAgent.complete
                                    if Config.VERBOSE:
                                        import traceback
                                        # Only record detailed error in verbose mode (use print instead of console.print to avoid interfering with completion menu)
                                        print(f"[补全错误] {str(e)}", file=sys.stderr)
                                        traceback.print_exc(file=sys.stderr)
                                    self.cache[text] = []
                            candidates.extend([c for c in self.cache[text] if c.startswith(text)])
                
                seen = set()
                for c in candidates:
                    if c in seen:
                        continue
                    seen.add(c)
                    yield Completion(c, start_position=-len(text))
        
        completer = HybridCompleter(base_items, llm_fetcher, system_commands)
        
        try:
            # For system commands (starting with '/'), we want auto-display completion when typing
            # For normal commands, only trigger LLM completion when pressing Tab
            # complete_while_typing=True allows system commands to auto-display when typing
            return pt_prompt(
                ANSI("\033[1;32m" + (prompt or "▸ ") + "\033[0m"),
                completer=completer,
                auto_suggest=None,  # 按 Tab 触发补全，手动采纳
                complete_while_typing=True,  # 系统指令（以'/'开头）输入时自动显示补全菜单
                reserve_space_for_menu=6,
            )
        except (KeyboardInterrupt, EOFError):
            return ""


# Global UI instance
ui = UI()
