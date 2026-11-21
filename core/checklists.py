"""Which shipping documents a shipment needs before arrival (DEV_PLAN 0.6b / A.4).

Single source of truth: seed_demo builds the checklists from it, and B's
missing_shipping_docs() rule should call it for `required_kinds`.
"""
from __future__ import annotations

from core.models import Shipment, ShipmentDocument

K = ShipmentDocument.Kind


def required_kinds(shipment: Shipment) -> list[str]:
    """Commercial invoice, transport document (B/L or AWB), packing list,
    inspection certificate for commodities moved by sea/air, and a certificate
    of origin whenever preferential origin is claimed."""
    kinds = [K.COMMERCIAL_INVOICE]
    if shipment.mode == "SEA":
        kinds.append(K.BILL_OF_LADING)
    elif shipment.mode == "AIR":
        kinds.append(K.AIRWAY_BILL)
    kinds.append(K.PACKING_LIST)
    if shipment.mode in ("SEA", "AIR"):
        kinds.append(K.INSPECTION_CERTIFICATE)
    if shipment.preferential_origin_claimed:
        kinds.append(K.CERTIFICATE_OF_ORIGIN)
    return [str(k) for k in kinds]


def missing_kinds(shipment: Shipment) -> list[str]:
    """Required kinds that are not RECEIVED or VERIFIED yet."""
    ok = set(
        shipment.documents.filter(
            status__in=[ShipmentDocument.Status.RECEIVED, ShipmentDocument.Status.VERIFIED]
        ).values_list("kind", flat=True)
    )
    return [k for k in required_kinds(shipment) if k not in ok]
