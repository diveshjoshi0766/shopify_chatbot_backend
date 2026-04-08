"""
EasyPost shipping API integration (REST via httpx).

Uses a single platform API key from settings; shipments are tagged with `reference`
containing tenant and user for multi-tenant traceability on one EasyPost account.
"""
