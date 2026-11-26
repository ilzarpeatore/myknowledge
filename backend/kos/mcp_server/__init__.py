"""MCP server: lets Claude (Code or Desktop) consult the library and propose changes.

Reads are free; anything that would change knowledge is stored as a *proposal*
(proposed concept, pending claim, proposed relationship) that the user reviews in
the app. Nothing is ever written as accepted, and every proposal is audited with
the token that made it.
"""
