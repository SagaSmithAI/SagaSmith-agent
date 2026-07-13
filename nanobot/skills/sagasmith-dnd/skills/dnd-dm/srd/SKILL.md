# D&D Rule Sources

This Skill carries three optional corpora:

- `references/`: SRD 5.2.1 / 2024 English.
- `references-2014-en/`: SRD 5.1 / 2014 English.
- `references-2014-zh/`: SRD 5.1 / 2014 Chinese convenience translation.

Runtime ingestion:

```powershell
sagasmith-dnd rules ingest --path "<file.md>" --edition 2024 --locale en --publication srd-5.2.1 --json
sagasmith-dnd rules ingest --path "<file.md>" --edition 2014 --locale en --publication srd-5.1 --json
sagasmith-dnd rules ingest --path "<file.md>" --edition 2014 --locale zh --publication srd-5.1-zh --json
```

The campaign rule profile controls search isolation. Do not search both editions
unless the user asks for a comparison. For a consequential 2014 ruling, use the
English corpus as the final reference when the Chinese translation is ambiguous.
