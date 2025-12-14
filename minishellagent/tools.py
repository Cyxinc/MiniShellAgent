#!/usr/bin/env python
# coding=utf-8

import os
import platform
import subprocess
import time
import sys
from typing import Tuple, Optional
from .config import Config
from .ui import ui

# Platform-specific imports
_IS_WINDOWS = platform.system() == "Windows"
_IS_UNIX = not _IS_WINDOWS

if _IS_UNIX:
    import select
    import pty
    import termios
    import fcntl
    import struct


class PersistentShell:
    """Persistent shell that keeps environment and cwd between commands."""
    
    def __init__(self, shell: str = None):
        """
        Initialize the persistent shell.

        Args:
            shell: overridden shell path (default from env)
        """
        if _IS_WINDOWS:
            # Use PowerShell if available, otherwise cmd.
            if shell:
                self.shell = shell
            else:
                # Prefer PowerShell if available, otherwise cmd
                powershell = os.getenv('POWERSHELL', 'powershell.exe')
                if os.system(f'where {powershell} >nul 2>&1') == 0:
                    self.shell = powershell
                else:
                    self.shell = os.getenv('COMSPEC', 'cmd.exe')
            # Temp files for Windows session state.
            import tempfile
            temp_dir = tempfile.gettempdir()
            self.state_file = os.path.join(temp_dir, f"minishellagent_shell_state_{os.getpid()}.bat")
            self.cwd_file = os.path.join(temp_dir, f"minishellagent_shell_cwd_{os.getpid()}.txt")
        else:
            # Unix-like temporary state files.
            self.shell = shell or os.getenv('SHELL', '/bin/zsh')
            self.state_file = f"/tmp/minishellagent_shell_state_{os.getpid()}.sh"
            self.cwd_file = f"/tmp/minishellagent_shell_cwd_{os.getpid()}.txt"
        # Initialize state files
        self._init_state()
    
    def _init_state(self):
        """Initialize shell state files."""
        try:
            initial_cwd = os.getcwd()
            with open(self.cwd_file, 'w', encoding='utf-8') as f:
                f.write(initial_cwd)
            with open(self.state_file, 'w', encoding='utf-8') as f:
                if _IS_WINDOWS:
                    # Windows batch snippet
                    f.write(f"@echo off\n")
                    f.write(f"cd /d \"{initial_cwd}\"\n")
                    for key, value in os.environ.items():
                        escaped_value = value.replace('"', '""')
                        f.write(f'set "{key}={escaped_value}"\n')
                else:
                    # Unix shell snippet
                    f.write(f"cd '{initial_cwd}'\n")
                    for key, value in os.environ.items():
                        escaped_value = value.replace("'", "'\"'\"'")
                        f.write(f"export {key}='{escaped_value}'\n")
        except Exception:
            pass
    
    def _get_terminal_size(self):
        """Get terminal size"""
        try:
            size = os.get_terminal_size()
            return size.lines, size.columns
        except Exception:
            return 24, 80  # Default value
    
    def execute(self, command: str, timeout: Optional[int] = 30) -> Tuple[bool, str, str]:
        """
        Run a command inside the persistent shell.

        Args:
            command: command line to execute
            timeout: timeout in seconds

        Returns:
            (success, stdout, stderr)
        """
        if _IS_WINDOWS:
            return self._execute_windows(command, timeout)
        else:
            return self._execute_unix(command, timeout)
    
    def _execute_windows(self, command: str, timeout: Optional[int] = 30) -> Tuple[bool, str, str]:
        """Simplified Windows execution (no pty support)."""
        try:
            import tempfile
            temp_dir = tempfile.gettempdir()
            exit_code_file = os.path.join(temp_dir, f"minishellagent_exit_code_{os.getpid()}.txt")
            
            # Build execution script
            if 'powershell' in self.shell.lower():
                # PowerShell script
                exec_script = f"""
# Load previous shell state before running command
if (Test-Path "{self.state_file}") {{
    & "{self.state_file}"
}}

# Run the user command
{command}
$LASTEXITCODE | Out-File -FilePath "{exit_code_file}" -Encoding utf8

# Save current working directory
(Get-Location).Path | Out-File -FilePath "{self.cwd_file}" -Encoding utf8
"""
                # Execute PowerShell script
                result = subprocess.run(
                    [self.shell, '-NoProfile', '-Command', exec_script],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=os.getcwd()
                )
            else:
                # CMD batch script
                exec_script = f"""@echo off
if exist "{self.state_file}" call "{self.state_file}"
{command}
echo %ERRORLEVEL% > "{exit_code_file}"
cd > "{self.cwd_file}"
"""
                # Execute CMD script
                result = subprocess.run(
                    [self.shell, '/c', exec_script],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=os.getcwd()
                )
            
            # Read exit code
            exit_code = result.returncode
            if os.path.exists(exit_code_file):
                try:
                    with open(exit_code_file, 'r', encoding='utf-8') as f:
                        exit_code = int(f.read().strip())
                    os.remove(exit_code_file)
                except Exception:
                    pass
            
            # Update working directory
            if os.path.exists(self.cwd_file):
                try:
                    with open(self.cwd_file, 'r', encoding='utf-8') as f:
                        new_cwd = f.read().strip()
                        if new_cwd and os.path.isdir(new_cwd):
                            os.chdir(new_cwd)
                except Exception:
                    pass
            
            success = exit_code == 0
            return success, result.stdout, result.stderr
            
        except subprocess.TimeoutExpired:
            return False, "", "Timeout"
        except Exception as e:
            return False, "", str(e)
    
    def _execute_unix(self, command: str, timeout: Optional[int] = 30) -> Tuple[bool, str, str]:
        """Run Unix shells via pty to capture live output."""
        try:
            # Build script: load state, run command, persist new state.
            
            import tempfile
            temp_dir = tempfile.gettempdir()
            exit_code_file = os.path.join(temp_dir, f"minishellagent_exit_code_{os.getpid()}.txt")
            
            # Determine shell configuration file path
            home = os.path.expanduser('~')
            if 'zsh' in self.shell:
                shell_rc = f"{home}/.zshrc"
            elif 'bash' in self.shell:
                shell_rc = f"{home}/.bashrc"
            else:
                shell_rc = ""
            
            # Build script that sources shell config for aliases/conda
            exec_script = f"""
# Source shell configuration if available (supports aliases and conda)
if [ -f "{shell_rc}" ]; then
    source "{shell_rc}" >/dev/null 2>&1
fi

# Load the saved shell state
if [ -f "{self.state_file}" ]; then
    source "{self.state_file}" >/dev/null 2>&1
fi

# Run the requested command and capture exit code
{command}
EXIT_CODE=$?

# Store exit code
echo $EXIT_CODE > "{exit_code_file}"

# Save current working directory
pwd > "{self.cwd_file}" 2>/dev/null || true

# Persist environment variables for future commands
env | while IFS='=' read -r key value; do
    # Escape single quotes in values
    escaped_value=$(printf '%s\n' "$value" | sed "s/'/'\\\\''/g")
    echo "export $key='$escaped_value'"
done > "{self.state_file}" 2>/dev/null || true
"""

            
            # Allocate a pty for the child shell
            master_fd, slave_fd = pty.openpty()
            
            # Set terminal size metadata
            rows, cols = self._get_terminal_size()
            winsize = struct.pack('HHHH', rows, cols, 0, 0)
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)
            
            # Fork the shell process
            pid = os.fork()
            
            if pid == 0:  # child process
                # Close master side, keep slave
                os.close(master_fd)
                
                # Point stdio/stderr to slave
                os.dup2(slave_fd, 0)  # stdin
                os.dup2(slave_fd, 1)  # stdout
                os.dup2(slave_fd, 2)  # stderr
                
                if slave_fd > 2:
                    os.close(slave_fd)
                
                # Adjust env so shell sees an interactive terminal
                env = os.environ.copy()
                env['TERM'] = env.get('TERM', 'xterm-256color')
                
                # Execute shell command; script already sources rc files
                os.execvpe(self.shell, [self.shell, '-c', exec_script], env)
                # If exec returns, it failed
                os._exit(1)
            
            else:  # parent process
                # Close the slave end
                os.close(slave_fd)
                
                # Make master fd non-blocking
                flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
                fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
                
                # Collect child output
                output = []
                start_time = time.time()
                
                try:
                    while True:
                        # Enforce timeout
                        if timeout and (time.time() - start_time) > timeout:
                            os.kill(pid, 9)  # SIGKILL
                            os.close(master_fd)
                            return False, "", "Timeout"
                        
                        # Check for child exit
                        pid_result, status = os.waitpid(pid, os.WNOHANG)
                        if pid_result != 0:
                            # Child finished; read remaining output
                            try:
                                while True:
                                    data = os.read(master_fd, 4096)
                                    if not data:
                                        break
                                    text = data.decode('utf-8', errors='replace')
                                    output.append(text)
                                    # Mirror to stdout live
                                    sys.stdout.write(text)
                                    sys.stdout.flush()
                            except OSError:
                                pass
                            break
                        
                        # Poll master fd for data
                        ready, _, _ = select.select([master_fd], [], [], 0.1)
                        if ready:
                            try:
                                data = os.read(master_fd, 4096)
                                if data:
                                    text = data.decode('utf-8', errors='replace')
                                    output.append(text)
                                    # Mirror to stdout live
                                    sys.stdout.write(text)
                                    sys.stdout.flush()
                            except OSError:
                                break
                
                finally:
                    os.close(master_fd)
                
                # Read the recorded exit code
                exit_code = 0
                if os.path.exists(exit_code_file):
                    try:
                        with open(exit_code_file, 'r') as f:
                            exit_code = int(f.read().strip())
                        os.remove(exit_code_file)
                    except Exception:
                        pass
                
                # Update the Python process working directory
                if os.path.exists(self.cwd_file):
                    try:
                        with open(self.cwd_file, 'r') as f:
                            new_cwd = f.read().strip()
                            if new_cwd and os.path.isdir(new_cwd):
                                os.chdir(new_cwd)
                    except Exception:
                        pass
                
                # Restore environment variables from the state file
                if os.path.exists(self.state_file):
                    try:
                        with open(self.state_file, 'r') as f:
                            for line in f:
                                line = line.strip()
                                if line.startswith('export ') and '=' in line:
                                    # Parse export KEY='value'
                                    parts = line[7:].split('=', 1)
                                    if len(parts) == 2:
                                        var_name = parts[0].strip()
                                        var_value = parts[1].strip().strip("'\"")
                                        os.environ[var_name] = var_value
                    except Exception:
                        pass
                
                stdout = ''.join(output)
                success = exit_code == 0
                return success, stdout, ""
            
        except Exception as e:
            return False, "", str(e)
    
    def get_cwd(self) -> str:
        """Get current working directory"""
        if os.path.exists(self.cwd_file):
            try:
                with open(self.cwd_file, 'r') as f:
                    cwd = f.read().strip()
                    if cwd and os.path.isdir(cwd):
                        return cwd
            except Exception:
                pass
        return os.getcwd()
    
    def close(self):
        """Clean up temporary files"""
        for file_path in [self.state_file, self.cwd_file]:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception:
                    pass
    
    def __del__(self):
        """Destructor, ensure cleanup"""
        self.close()


class TerminalTool:
    def __init__(self, safe_mode: bool = True, use_persistent_shell: bool = False):
        """
        Initialize terminal tool
        
        Args:
            safe_mode: Whether to enable safe mode
            use_persistent_shell: Whether to use persistent shell (simulate normal terminal behavior)
        """
        self.safe_mode = safe_mode or Config.SAFE_MODE
        self.use_persistent_shell = use_persistent_shell
        self.persistent_shell = PersistentShell() if use_persistent_shell else None
    
    def execute(
        self, 
        command: str, 
        timeout: Optional[int] = 30,
        require_confirm: bool = True,
        show_status: bool = True
    ) -> Tuple[bool, str, str]:
        # Final safety check (re-validate before executing)
        if self.safe_mode and Config.is_dangerous_command(command):
            ui.print_error(f" 检测到危险命令，已拒绝执行: {command}")
            ui.print_warning("如需执行此命令，请使用 --no-safe-mode 参数（不推荐）")
            return False, "", "Dangerous command blocked"
        
        # Validate command format
        is_valid, msg = self.validate_command(command)
        if not is_valid:
            ui.print_error(f"命令验证失败: {msg}")
            return False, "", msg
        
        # Check sudo commands (always require confirmation)
        command_lower = command.lower().strip()
        is_sudo_command = command_lower.startswith("sudo ")
        if is_sudo_command:
            ui.print_warning("  检测到sudo命令，需要管理员权限")
            # Always confirm sudo commands, even in auto mode
            if not ui.confirm("确认执行此sudo命令？", default=False):
                ui.print_warning("sudo命令执行已取消")
                return False, "", "User cancelled sudo command"
        
        # Check for other high-risk commands (confirm even in auto mode)
        high_risk_keywords = ["rm -rf", "mkfs", "fdisk", "dd if=", "dd of=", "format", "wipe"]
        is_high_risk = any(keyword in command_lower for keyword in high_risk_keywords)
        if is_high_risk and self.safe_mode:
            ui.print_warning("  检测到高风险命令")
            # High-risk commands always require confirmation
            if not ui.confirm("确认执行此高风险命令？", default=False):
                ui.print_warning("高风险命令执行已取消")
                return False, "", "User cancelled high-risk command"
        
        # Show command status only when needed (agent mode)
        if show_status:
            ui.print_command(command, "pending")
        
        # Confirm regular commands (sudo/high-risk already confirmed)
        if require_confirm and not is_sudo_command and not is_high_risk:
            if not ui.confirm("是否执行此命令？", default=True):
                ui.print_warning("命令执行已取消")
                return False, "", "User cancelled"
        
        # Use persistent shell when configured
        if self.use_persistent_shell and self.persistent_shell:
            try:
                success, stdout, stderr = self.persistent_shell.execute(command, timeout=timeout)
                
                # In pty mode output already prints live, but ensure visibility
                if show_status:
                    # Display error if command failed
                    if not success and stderr:
                        ui.print_command(command, "error")
                        ui.print_output(stderr, is_error=True)
                    # If the command succeeded but output is missing or short, ensure it is displayed
                    elif success and stdout and stdout.strip():
                        # Display output to keep user informed
                        if not Config.VERBOSE:
                            # Non-verbose mode already printed live output
                            pass
                        else:
                            # Verbose mode prints again for clarity
                            ui.print_output(stdout, is_error=False)
                
                return success, stdout, stderr
            except Exception as e:
                ui.print_error(f"命令执行失败: {str(e)}")
                return False, "", str(e)
        
        # Fallback execution path (non-persistent shell)
        # Detect commands that may change the working directory
        command_lower = command.strip().lower()
        is_directory_change_cmd = (
            command_lower.startswith('cd ') or
            command_lower.startswith('pushd ') or
            command_lower == 'popd' or
            command_lower.startswith('popd ')
        )
        
        try:
            # Handle directory-changing commands specially to update cwd
            if is_directory_change_cmd:
                # Run command and capture new working directory
                # Use shell execution to honor aliases/functions
                shell_cmd = f'{command} && pwd'
                result = subprocess.run(
                    shell_cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout
                )
                
                success = result.returncode == 0
                stdout = result.stdout.strip()
                stderr = result.stderr.strip()
                
                    # On success, parse the emitted pwd line for new cwd
                if success:
                    lines = stdout.split('\n')
                    # The pwd output is usually the last line
                    new_dir = lines[-1] if lines else None
                    if new_dir and os.path.isdir(new_dir):
                        try:
                            os.chdir(new_dir)
                        except Exception:
                            pass
                    
                    # Manage output display:
                    # - Pure cd/pushd/popd commands: hide the appended pwd line
                    # - Combined commands: keep the user-visible output
                    is_pure_dir_cmd = (
                        '&&' not in command_lower and 
                        ';' not in command_lower and 
                        '|' not in command_lower
                    )
                    
                    if is_pure_dir_cmd:
                        # Pure directory change commands hide the injected pwd line
                        stdout = "" if command_lower.startswith('cd ') else '\n'.join(lines[:-1]) if len(lines) > 1 else ""
                    else:
                        # Combined commands keep user output (drop injected pwd)
                        stdout = '\n'.join(lines[:-1]) if len(lines) > 1 else stdout
            else:
                # Non-directory commands run normally
                result = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout
                )
                
                success = result.returncode == 0
                stdout = result.stdout.strip()
                stderr = result.stderr.strip()
                
                # Even non-directory commands might change cwd, so recheck
                try:
                    pwd_result = subprocess.run(
                        'pwd',
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=1
                    )
                    if pwd_result.returncode == 0:
                        new_dir = pwd_result.stdout.strip()
                        if new_dir and os.path.isdir(new_dir) and new_dir != os.getcwd():
                            os.chdir(new_dir)
                except Exception:
                    pass
            
            if success:
                if stdout:
                    ui.print_output(stdout, is_error=False)
            else:
                ui.print_command(command, "error")
                if stderr:
                    ui.print_output(stderr, is_error=True)
            
            return success, stdout, stderr
            
        except subprocess.TimeoutExpired:
            ui.print_error(f"命令执行超时 (>{timeout}s)")
            return False, "", "Timeout"
        except Exception as e:
            ui.print_error(f"命令执行失败: {str(e)}")
            return False, "", str(e)
    
    def validate_command(self, command: str) -> Tuple[bool, str]:
        """Validate command safety"""
        if not command or not command.strip():
            return False, "命令不能为空"
        
        # Security checks
        if self.safe_mode and Config.is_dangerous_command(command):
            return False, f"危险命令已被阻止: {command}"
        
        # Guard against excessively long commands
        if len(command) > 10000:
            return False, "命令过长，可能存在安全风险"
        
        # Look for obvious injection patterns
        dangerous_chars_combinations = [
            (";", "rm"),
            ("&&", "rm"),
            ("||", "rm"),
            ("`", "rm"),
            ("$(", "rm"),
        ]
        command_lower = command.lower()
        for combo in dangerous_chars_combinations:
            if combo[0] in command and combo[1] in command_lower:
                # Check if the combo is normal or a potential injection
                # Simple heuristic: semicolon/&& followed by rm could be unsafe
                parts = command.split(combo[0])
                for part in parts[1:]:  # Check parts after semicolon/&&
                    if part.strip().lower().startswith(combo[1]):
                        if self.safe_mode:
                            return False, f"检测到可能的命令注入: {command}"
        
        return True, "OK"
    
    def get_description(self) -> dict:
        return {
            "name": "terminal",
            "description": "执行终端命令并返回结果",
            "parameters": {
                "command": {
                    "type": "string",
                    "description": "要执行的shell命令"
                }
            }
        }
    
    def close(self):
        """Close persistent shell (if used)"""
        if self.persistent_shell:
            self.persistent_shell.close()

