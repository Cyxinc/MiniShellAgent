#!/usr/bin/env python
# coding=utf-8
"""LLM abstraction layer - supports multiple LLM backends"""

from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Union
import json
from .config import Config


class BaseLLM(ABC):
    """LLM base class"""
    
    def __init__(self, **kwargs):
        self.config = kwargs
        self.model_name = None  # Model name
        self.total_prompt_tokens = 0  # Cumulative input token count
        self.total_completion_tokens = 0  # Cumulative output token count
        self.total_tokens = 0  # Cumulative total token count
        self.call_count = 0  # Call count
        
    @abstractmethod
    def generate(
        self, 
        messages: List[Dict[str, str]], 
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        """Generate response"""
        pass
    
    def chat(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Simple chat interface"""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return self.generate(messages)
    
    def get_model_name(self) -> str:
        """Get model name"""
        return self.model_name or "Unknown"
    
    def get_token_stats(self) -> Dict[str, int]:
        """Get token statistics"""
        return {
            "prompt_tokens": self.total_prompt_tokens,
            "completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_tokens,
            "call_count": self.call_count,
        }
    
    def reset_token_stats(self):
        """Reset token statistics"""
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_tokens = 0
        self.call_count = 0


class OpenAILLM(BaseLLM):
    """OpenAI-compatible LLM"""
    
    def __init__(
        self, 
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs
    ):
        super().__init__(**kwargs)
        
        # Pull values from config or keyword args
        config = Config.get_llm_config("openai")
        self.api_key = api_key or config["api_key"]
        self.base_url = base_url or config["base_url"]
        self.model = model or config["model"]
        self.model_name = self.model  # Set model name
        
        if not self.api_key:
            raise ValueError("OpenAI API key is required. Set OPENAI_API_KEY in .env")
        
        # Initialize the OpenAI client
        try:
            from openai import OpenAI
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url
            )
        except ImportError:
            raise ImportError("Please install openai: pip install openai")
    
    def generate(
        self, 
        messages: List[Dict[str, str]], 
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        timeout: Optional[int] = None,
        **kwargs
    ) -> str:
        """Generate response"""
        try:
            # Respect configured timeout when not provided
            if timeout is None:
                from .config import Config
                timeout = Config.LLM_TIMEOUT
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                **kwargs
            )
            content = response.choices[0].message.content
            if content is None:
                raise RuntimeError("LLM returned None content. This may indicate an API error or model issue.")
            
            # Track token usage from OpenAI response
            if hasattr(response, 'usage') and response.usage:
                usage = response.usage
                self.total_prompt_tokens += usage.prompt_tokens or 0
                self.total_completion_tokens += usage.completion_tokens or 0
                self.total_tokens += usage.total_tokens or 0
                self.call_count += 1
            
            return content
        except Exception as e:
            error_msg = str(e)
            # Translate timeout-related errors to user-friendly message
            if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
                raise RuntimeError(f"LLM 调用超时（{timeout}秒）。请检查网络连接或增加超时时间。")
            raise RuntimeError(f"OpenAI API error: {e}")


class LocalLlamaLLM(BaseLLM):
    """Local Llama model"""
    
    def __init__(
        self,
        model_path: Optional[str] = None,
        n_ctx: int = 4096,
        n_gpu_layers: int = 0,
        **kwargs
    ):
        super().__init__(**kwargs)
        
        # Pull values from config or keyword args
        config = Config.get_llm_config("llama")
        self.model_path = model_path or config["model_path"]
        self.n_ctx = n_ctx or config["n_ctx"]
        self.n_gpu_layers = n_gpu_layers or config["n_gpu_layers"]
        
        if not self.model_path:
            raise ValueError("Llama model path is required. Set LLAMA_MODEL_PATH in .env")
        
        # Derive model name from the path basename
        import os
        self.model_name = os.path.basename(self.model_path) if self.model_path else "Llama"
        
        # Initialize llama-cpp-python backend
        try:
            from llama_cpp import Llama
            self.llm = Llama(
                model_path=self.model_path,
                n_ctx=self.n_ctx,
                n_gpu_layers=self.n_gpu_layers,
                verbose=Config.VERBOSE
            )
        except ImportError:
            raise ImportError("Please install llama-cpp-python: pip install llama-cpp-python")
    
    def generate(
        self, 
        messages: List[Dict[str, str]], 
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        """Generate response"""
        try:
            response = self.llm.create_chat_completion(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens or 1024,
                **kwargs
            )
            content = response["choices"][0]["message"]["content"]
            if content is None:
                raise RuntimeError("LLM returned None content. This may indicate a model error.")
            return content
        except Exception as e:
            raise RuntimeError(f"Llama generation error: {e}")


class LLMFactory:
    """LLM factory class"""
    
    _llm_registry = {
        "openai": OpenAILLM,
        "llama": LocalLlamaLLM,
    }
    
    @classmethod
    def create(cls, llm_type: str = "openai", **kwargs) -> BaseLLM:
        """Create LLM instance"""
        if llm_type not in cls._llm_registry:
            raise ValueError(f"Unknown LLM type: {llm_type}. Available: {list(cls._llm_registry.keys())}")
        
        llm_class = cls._llm_registry[llm_type]
        return llm_class(**kwargs)
    
    @classmethod
    def register(cls, name: str, llm_class: type):
        """Register new LLM type"""
        cls._llm_registry[name] = llm_class

