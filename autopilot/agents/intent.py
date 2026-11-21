"""intent_agent: a plain-language order -> RoutingRequest (PLAN_B §B2.2 #1)."""
from __future__ import annotations

from google.adk.agents import LlmAgent

from autopilot.domain.schemas import CargoClass, Preset, RoutingRequest
from autopilot.domain.tables import LOCATIONS
from autopilot.runner import call_config, gemini

_PLACES = ", ".join(sorted(loc.name for loc in LOCATIONS.values()))

INSTRUCTION = f"""You turn a logistics order written in plain language into a RoutingRequest.

Rules:
- origin / destination: use the closest name from this list, spelled exactly: {_PLACES}.
- cargo.cargo_class, one of {[c.value for c in CargoClass]}:
  ores, concentrates, grain -> DRY_BULK; oils, liquids in bulk -> LIQUID_BULK; hazardous, flammable,
  chemicals with an IMDG/ADR class -> DANGEROUS_GOODS (set dg_class if stated); food, pharma, anything
  "cold"/"frozen"/"reefer" -> PERISHABLE_REEFER; gold, jewels, cash, high-value electronics -> HIGH_VALUE;
  machinery too large for a container -> OVERSIZE; everything else -> CONTAINER_GENERAL.
- cargo.weight_t in tonnes; cargo.value_eur in EUR. Copy numbers from the order only.
  If the value is not stated, set value_eur to 0. Never estimate a number.
- deadline: ISO date. Resolve relative dates ("in 3 weeks", "by Mar 3") against TODAY given in the message.
- preset, one of {[p.value for p in Preset]}: "cheapest"/"low cost" -> CHEAPEST, "asap"/"fastest" -> FASTEST,
  "safe"/"no risk" -> MOST_RELIABLE, "green"/"low emissions" -> GREENEST, otherwise BEST.
- urgency: "high" only if the order says urgent/asap; "low" if it says no rush; else "normal".
- constraints: banned_modes only when the order excludes a mode ("no air"); avoid_corridors only when it
  names a route to avoid (e.g. "avoid the Red Sea" -> ["BAB_EL_MANDEB", "SUEZ"]).
- Leave weights empty: the preset sets them.
Fields already given as FORM in the message are authoritative: copy them unchanged.
"""

intent_agent = LlmAgent(
    name="intent_agent",
    model=gemini(),
    generate_content_config=call_config(),
    description="Turns a plain-language shipping order into solver parameters.",
    instruction=INSTRUCTION,
    output_schema=RoutingRequest,
    output_key="routing_request",
)
