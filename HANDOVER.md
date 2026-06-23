# Handover — D&D WebUI Integration & Bug Fix

## 401 Unauthorized Bug

### Symptom
`http://127.0.0.1:18765/` returns `401 Unauthorized` while `/webui/bootstrap` works fine.

### Root Cause
The custom `_dispatch_dnd_routes()` function called `check_api_token()` BEFORE path matching, rejecting ALL HTTP requests (including SPA root `/`) that didn't carry an API token.

### Fix
Add path prefix guard before auth check:

```python
# ws_http.py: _dispatch_dnd_routes()
async def _dispatch_dnd_routes(self, connection, request, got):
    if not got.startswith("/api/dnd"):
        return None   # let request fall through to static serving

    if not self.check_api_token(request):
        return _http_error(401, "Unauthorized")
    # ... route matching
```

File: `nanobot/webui/ws_http.py`

### Lesson
When adding custom routes to `GatewayHTTPHandler._dispatch_resolved()`, always return `None` for non-matching paths so requests can fall through to static SPA serving and 404 handling. Never check auth before route matching.

---

## Session Summary

### 1. ChromaDB Vector Store
- `nanobot/dnd/vector/` — VectorStore client + chroma_dense_search()
- HNSW indexing for dnd_rules + dnd_modules collections
- Embedding decoupled from SQL transactions
- CLI: `vector migrate|status|verify|reindex`

### 2. Three Bundled Rule Sets  
- 2024 EN SRD 5.2.1 (2,684 chunks)
- 2014 EN SRD 5.1 (3,524 chunks)  
- 2014 ZH SRD 5.1 (~2,000 chunks)
- Auto-ingest on first `dnd_rules` access (lazy)

### 3. Campaign Rule Selection
- `CampaignService.create()/start()` accept rule_set_id + publication_ids
- `CampaignInfo` includes rule_set and publications fields

### 4. Character Library (PC/NPC Decoupled)
- campaign_id nullable, character_type "pc"|"npc"
- 12 lore fields: race, alignment, personality, backstory, appearance...
- `dnd_character` tool: create/list/show/update/bind/unbind

### 5. NPC Attitude & World State
- Faction relations: 5-level attitude scale
- NPC status stored by character_id in WorldState JSON
- `WorldService` in nanobot/dnd/db/world.py

### 6. D&D WebUI Dashboard
- REST API: 16 endpoints at /api/dnd/*
- Components: CampaignPanel, CharacterPanel, WorldPanel, CharacterForm
- Route: #/dnd, sidebar Swords button

### 7. Multiplayer Campaign Room  
- Peer broadcast (skips agent loop for player messages)
- room_join/leave presence protocol
- RoomView: shared chat with player list
- Route: #/room/<campaign_id>

### 8. Configuration
- config/config.json (local, gitignored) + config.example.json (template)
- 15 platforms enabled, mention-only for group chat
- Gateway reads from local config with ChromaDB
