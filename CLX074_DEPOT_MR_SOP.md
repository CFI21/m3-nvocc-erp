# M3 CLX-074 Depot / Inspection / Damage & M&R SOP

1. Gate returned equipment into the existing Container Master custody/depot context.
2. Start an EMPTY_RETURN inspection and place the unit on inspection hold.
3. Capture each damage item by component, location and severity; link photos through existing container_documents.
4. Flag cleaning/repair needs and keep damaged units on HOLD.
5. Submit M&R estimate with labour, material, third-party and cleaning components.
6. Require maker/checker approval before repair execution.
7. Create repair execution through the existing equipment_work_items queue.
8. Complete repair and post actual M&R cost into the existing container_financial_ledger.
9. Reinspect CSC/safety/condition after repair.
10. PASS clears holds and returns the same physical container to AVAILABLE; FAIL returns it to HOLD and opens an exception.
11. Attribute damage liability to owner/lessor/agent/customer/shipper/haulier where applicable.
12. Review repair overdue, estimate overrun and reinspection-failure exceptions and management KPIs.

Production traffic, live providers and real-money execution remain OFF.
