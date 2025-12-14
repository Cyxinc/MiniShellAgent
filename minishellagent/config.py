#!/usr/bin/env python
# coding=utf-8
"""Configuration management module"""

import os
import platform
from typing import Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv(override=True)


class Config:
    """Global configuration class"""
    
    # LLM configuration
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "deepseek-v3.2")
    
    # Local Llama configuration
    LLAMA_MODEL_PATH: str = os.getenv("LLAMA_MODEL_PATH", "")
    LLAMA_N_CTX: int = int(os.getenv("LLAMA_N_CTX", "4096"))
    LLAMA_N_GPU_LAYERS: int = int(os.getenv("LLAMA_N_GPU_LAYERS", "0"))
    
    # Agent configuration
    MAX_STEPS: int = int(os.getenv("MAX_STEPS", "10"))
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))
    MAX_IDLE_STEPS: int = int(os.getenv("MAX_IDLE_STEPS", "2"))  # Max consecutive invalid/empty responses, auto end
    LLM_TIMEOUT: int = int(os.getenv("LLM_TIMEOUT", "120"))  # LLM call timeout (seconds)

    # History configuration (for completion optimization)
    HISTORY_FILE: str = os.getenv("HISTORY_FILE", 
                                   "~/.zsh_history" if platform.system() != "Windows" 
                                   else os.path.expanduser("~\\AppData\\Roaming\\Microsoft\\Windows\\PowerShell\\PSReadLine\\ConsoleHost_history.txt"))
    HISTORY_MAX_LINES: int = int(os.getenv("HISTORY_MAX_LINES", "200"))
    
    # Safety configuration
    SAFE_MODE: bool = os.getenv("SAFE_MODE", "true").lower() == "true"
    
    # Dangerous command patterns (comprehensive detection)
    DANGEROUS_COMMANDS: list = [
        # Delete system critical directories
        "rm -rf /",
        "rm -rf /bin",
        "rm -rf /usr",
        "rm -rf /etc",
        "rm -rf /var",
        "rm -rf /sys",
        "rm -rf /proc",
        "rm -rf /boot",
        "rm -rf /root",
        # Format commands
        "mkfs",
        "fdisk",
        "parted",
        "dd if=",
        "dd of=",
        # Fork bomb
        ":(){:|:&};:",
        # Permission modification (system level)
        "chmod -R 777 /",
        "chmod -R 000 /",
        "chown -R",
        # sudo related (requires special confirmation)
        "sudo rm",
        "sudo mkfs",
        "sudo fdisk",
        "sudo dd",
        "sudo chmod",
        "sudo chown",
        # System service operations
        "systemctl stop",
        "systemctl disable",
        "service stop",
        # Network related dangerous operations
        "iptables -F",
        "iptables -X",
        # Environment variable override
        "export PATH=",
        "unset PATH",
    ]
    
    # Dangerous command patterns (regex, more precise)
    DANGEROUS_PATTERNS: list = [
        r"rm\s+-rf\s+/[^/]",  # rm -rf / prefix
        r"rm\s+-rf\s+/(bin|usr|etc|var|sys|proc|boot|root)",  # Delete system directories
        r"mkfs\.?\w*\s+/",  # Format root partition
        r"dd\s+if=.*\s+of=/dev/",  # dd to device
        r"chmod\s+[0-7]{3}\s+/",  # Modify root directory permissions
        r"sudo\s+(rm|mkfs|fdisk|dd|chmod|chown)",  # sudo dangerous commands
        r":\(\)\{.*:\|.*&.*\};:",  # fork bomb variants
    ]
    
    # UI configuration
    ENABLE_COLORS: bool = os.getenv("ENABLE_COLORS", "true").lower() == "true"
    VERBOSE: bool = os.getenv("VERBOSE", "false").lower() == "true"
    
    @classmethod
    def get_project_root(cls) -> str:
        """Get project root directory (parent directory of minishellagent package)"""
        # __file__ is the path of current file (config.py)
        # Its parent's parent is the project root
        current_file = os.path.abspath(__file__)
        project_root = os.path.dirname(os.path.dirname(current_file))
        return project_root
    
    @classmethod
    def get_user_config_file(cls) -> str:
        """Get user config file path (under project root)"""
        return os.path.join(cls.get_project_root(), '.minishellagent_config.json')
    
    @classmethod
    def load_user_config(cls) -> dict:
        """Load user personal configuration"""
        import json
        config_file = cls.get_user_config_file()
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}
    
    @classmethod
    def save_user_config(cls, config: dict):
        """Save user personal configuration"""
        import json
        try:
            config_file = cls.get_user_config_file()
            config_dir = os.path.dirname(config_file)
            if config_dir:
                os.makedirs(config_dir, exist_ok=True)
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            # Save failure doesn't affect program execution
            pass
    
    @classmethod
    def get_llm_config(cls, llm_type: str = "openai") -> dict:
        """Get LLM configuration"""
        if llm_type == "openai":
            return {
                "api_key": cls.OPENAI_API_KEY,
                "base_url": cls.OPENAI_BASE_URL,
                "model": cls.OPENAI_MODEL,
            }
        elif llm_type == "llama":
            return {
                "model_path": cls.LLAMA_MODEL_PATH,
                "n_ctx": cls.LLAMA_N_CTX,
                "n_gpu_layers": cls.LLAMA_N_GPU_LAYERS,
            }
        else:
            raise ValueError(f"Unknown LLM type: {llm_type}")
    
    @classmethod
    def is_dangerous_command(cls, command: str) -> bool:
        """Check if command is dangerous (multi-layer detection)"""
        if not cls.SAFE_MODE:
            return False
        
        import re
        
        command_lower = command.lower().strip()
        command_original = command.strip()
        
        # Layer 1: Simple string matching
        for dangerous in cls.DANGEROUS_COMMANDS:
            if dangerous.lower() in command_lower:
                return True
        
        # Layer 2: Regex matching (more precise)
        for pattern in cls.DANGEROUS_PATTERNS:
            if re.search(pattern, command_lower, re.IGNORECASE):
                return True
        
        # Layer 3: Check dangerous operation combinations
        # Check if contains sudo and involves system critical operations
        if "sudo" in command_lower:
            dangerous_sudo_ops = ["rm", "mkfs", "fdisk", "dd", "chmod", "chown", "format", "wipe"]
            if any(op in command_lower for op in dangerous_sudo_ops):
                return True
        
        # Layer 4: Check if trying to delete system directories outside user home
        # Allow deleting files in current user directory, but prevent deleting system directories
        if "rm" in command_lower and ("-rf" in command_lower or "-r" in command_lower):
            # Check if contains system critical paths
            system_paths = ["/bin", "/usr", "/etc", "/var", "/sys", "/proc", "/boot", "/root", "/sbin", "/lib"]
            for path in system_paths:
                if path in command_original:
                    return True
        
        return False

