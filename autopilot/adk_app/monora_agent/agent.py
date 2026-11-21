"""Entry point for the ADK dev UI:  adk web autopilot/adk_app

TODO(B): django.setup() and expose the real root_agent (B.6).
"""
import os

from google.adk.agents import Agent

root_agent = Agent(
    name="monora_agent",
    model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    description="Monora logistics expense autopilot (placeholder).",
    instruction="You are a placeholder. Say hello and explain the real agents are not wired yet.",
)
