import { useState } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { DndCharacterFormData, DndCharacterInfo } from "@/lib/types";
import { createDndCharacter, updateDndCharacter } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Props {
  token: string;
  defaultType: "pc" | "npc";
  initial?: DndCharacterInfo;
  onDone: () => void;
  onCancel: () => void;
}

export function CharacterForm({ token, defaultType, initial, onDone, onCancel }: Props) {
  const isNpc = defaultType === "npc";
  const [tab, setTab] = useState<"stats" | "lore">("stats");
  const [saving, setSaving] = useState(false);

  const [form, setForm] = useState<DndCharacterFormData>({
    name: initial?.name || "",
    character_type: defaultType,
    campaign_id: initial?.campaign_id || undefined,
    player_name: initial?.player_name || undefined,
    class_name: initial?.class_name || "",
    level: initial?.level || 1,
    hp: initial?.hp || 10,
    max_hp: initial?.max_hp || 10,
    armor_class: initial?.armor_class || 10,
    race: initial?.race || "",
    background: initial?.background || "",
    alignment: initial?.alignment || "",
    personality_traits: initial?.personality_traits || "",
    ideals: initial?.ideals || "",
    bonds: initial?.bonds || "",
    flaws: initial?.flaws || "",
    appearance: initial?.appearance || "",
    backstory: initial?.backstory || "",
    goals: initial?.goals || "",
    notes: initial?.notes || "",
    portrait_url: initial?.portrait_url || "",
  });

  const set = (k: keyof DndCharacterFormData, v: string | number | undefined) =>
    setForm((prev) => ({ ...prev, [k]: v }));

  const handleSave = async () => {
    setSaving(true);
    try {
      if (initial) {
        const fields: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(form)) {
          if (v !== (initial as any)[k]) (fields as any)[k] = v;
        }
        await updateDndCharacter(token, initial.id, fields);
      } else {
        await createDndCharacter(token, form);
      }
      onDone();
    } finally {
      setSaving(false);
    }
  };

  const inputCls = "w-full rounded-md border border-border bg-background px-2.5 py-1.5 text-sm";
  const labelCls = "text-xs text-muted-foreground mb-0.5 block";

  return (
    <div className="border border-border rounded-lg p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="font-medium text-sm">
          {initial ? `Edit: ${initial.name}` : `New ${isNpc ? "NPC" : "PC"}`}
        </h3>
        <button onClick={onCancel} className="text-muted-foreground hover:text-foreground"><X className="w-4 h-4" /></button>
      </div>

      <div className="flex gap-1">
        {(["stats", "lore"] as const).map((tb) => (
          <button
            key={tb}
            onClick={() => setTab(tb)}
            className={cn("px-2 py-0.5 text-xs rounded", tab === tb ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted")}
          >
            {tb === "stats" ? "Stats" : "Lore"}
          </button>
        ))}
      </div>

      {tab === "stats" && (
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className={labelCls}>Name *</label>
            <input className={inputCls} value={form.name} onChange={(e) => set("name", e.target.value)} />
          </div>
          <div>
            <label className={labelCls}>Type</label>
            <select className={inputCls} value={form.character_type} onChange={(e) => set("character_type", e.target.value)}>
              <option value="pc">PC</option>
              <option value="npc">NPC</option>
            </select>
          </div>
          <div>
            <label className={labelCls}>Class</label>
            <input className={inputCls} value={form.class_name} onChange={(e) => set("class_name", e.target.value)} />
          </div>
          <div>
            <label className={labelCls}>Race</label>
            <input className={inputCls} value={form.race} onChange={(e) => set("race", e.target.value)} />
          </div>
          <div><label className={labelCls}>Level</label><input className={inputCls} type="number" value={form.level} onChange={(e) => set("level", +e.target.value)} /></div>
          <div><label className={labelCls}>HP</label><input className={inputCls} type="number" value={form.hp} onChange={(e) => set("hp", +e.target.value)} /></div>
          <div><label className={labelCls}>Max HP</label><input className={inputCls} type="number" value={form.max_hp} onChange={(e) => set("max_hp", +e.target.value)} /></div>
          <div><label className={labelCls}>AC</label><input className={inputCls} type="number" value={form.armor_class} onChange={(e) => set("armor_class", +e.target.value)} /></div>
          <div>
            <label className={labelCls}>Alignment</label>
            <select className={inputCls} value={form.alignment} onChange={(e) => set("alignment", e.target.value)}>
              <option value="">—</option>
              {["LG","NG","CG","LN","N","CN","LE","NE","CE"].map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
          </div>
          <div><label className={labelCls}>Background</label><input className={inputCls} value={form.background} onChange={(e) => set("background", e.target.value)} /></div>
          {!isNpc && (
            <div><label className={labelCls}>Player</label><input className={inputCls} value={form.player_name || ""} onChange={(e) => set("player_name", e.target.value)} /></div>
          )}
        </div>
      )}

      {tab === "lore" && (
        <div className="space-y-2">
          <div><label className={labelCls}>Personality</label><textarea className={inputCls} rows={2} value={form.personality_traits} onChange={(e) => set("personality_traits", e.target.value)} /></div>
          <div><label className={labelCls}>Appearance</label><textarea className={inputCls} rows={2} value={form.appearance} onChange={(e) => set("appearance", e.target.value)} /></div>
          <div><label className={labelCls}>Backstory</label><textarea className={inputCls} rows={3} value={form.backstory} onChange={(e) => set("backstory", e.target.value)} /></div>
          <div className="grid grid-cols-2 gap-2">
            <div><label className={labelCls}>Ideals</label><input className={inputCls} value={form.ideals} onChange={(e) => set("ideals", e.target.value)} /></div>
            <div><label className={labelCls}>Bonds</label><input className={inputCls} value={form.bonds} onChange={(e) => set("bonds", e.target.value)} /></div>
            <div><label className={labelCls}>Flaws</label><input className={inputCls} value={form.flaws} onChange={(e) => set("flaws", e.target.value)} /></div>
            <div><label className={labelCls}>Goals</label><input className={inputCls} value={form.goals} onChange={(e) => set("goals", e.target.value)} /></div>
          </div>
          {isNpc && <div><label className={labelCls}>DM Notes</label><textarea className={inputCls} rows={2} value={form.notes} onChange={(e) => set("notes", e.target.value)} /></div>}
        </div>
      )}

      <div className="flex justify-end gap-2 pt-1">
        <Button size="sm" variant="outline" onClick={onCancel}>Cancel</Button>
        <Button size="sm" onClick={handleSave} disabled={saving || !form.name}>
          {saving ? "Saving..." : initial ? "Update" : "Create"}
        </Button>
      </div>
    </div>
  );
}
