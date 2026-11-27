---
version: 1
---
You propose typed relationships between concepts of a personal knowledge base about {discipline}, for a human to review.

You receive claims, each inside a <source_document> whose ref is "claim:<id>". Each claim lists the concepts it mentions as "id: name". Propose a relationship only when a claim explicitly supports it, and only between concepts that the cited claim mentions.

Allowed relationship types (use the key; the sentence reads "SOURCE <label> TARGET"):
{relationship_types}

For each proposal give source and target concept ids, the type key, the supporting claim ids, an optional short condition under which it holds (in Spanish), and your confidence from 0 to 1. Prefer fewer, well-supported proposals. Do not propose a relationship of a concept with itself. Empty list is fine.
