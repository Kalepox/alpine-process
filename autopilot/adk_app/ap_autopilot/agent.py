"""Entry point for the ADK dev UI:  adk web autopilot/adk_app

TODO(agentics): replace the placeholder with the real root agent.
"""
import os

from google.adk.agents import Agent

root_agent = Agent(
    name="ap_autopilot",
    model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    description="Back-office autopilot (placeholder).",
    instruction="You are a placeholder agent. Say hello and explain that the real pipeline is not wired yet.",
)
