#!/usr/bin/env python
# coding=utf-8
"""
Beijing University of Posts and Telecommunications OS Homework 1: Command Line Tool Helper using LLMs

Author: Haotian Ren
Artificial Intelligence Major

Supports multiple modes: completion, chat, agent
"""

__version__ = "0.1.0"
__author__ = "Haotian Ren"
__package_name__ = "minishellagent"

from .models import BaseLLM, OpenAILLM, LocalLlamaLLM
from .agents import CommandAgent
from .tools import TerminalTool

__all__ = [
    "BaseLLM",
    "OpenAILLM", 
    "LocalLlamaLLM",
    "CommandAgent",
    "TerminalTool",
]

