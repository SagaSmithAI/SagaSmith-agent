import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Plus, User, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { DndCharacterInfo } from "@/lib/types";
import { fetchDndCharacters, fetchDndCharacter } from "@/lib/api";
import { CharacterForm } from "./CharacterForm";
import { cn } from "@/lib/utils";

interface Props {
  token: string;
}

export function CharacterPanel({ token }: Props) {
  const { t } = useTranslation();
  const [characters, setCharacters] = useState<DndCharacterInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"all" | "pc" | "npc">("all");
  const [showForm, setShowForm] = useState(false);
  const [selected, setSelected] = useState<DndCharacterInfo | null>(null);
  const [editing, setEditing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = filter !== "all" ? { character_type: filter } : undefined;
      setCharacters(await fetchDndCharacters(token, "", params));
    } finally {
      setLoading(false);
    }
  }, [token, filter]);

  useEffect(() => { load(); }, [load]);

  const onCreated = () => {
    setShowForm(false);
    load();
  };

  const onUpdated = () => {
    setEditing(false);
    load();
  };

  if (loading) return <div className="text-muted-foreground text-sm p-4">Loading...</div>;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex gap-1">
          {(["all", "pc", "npc"] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={cn(
                "px-2.5 py-1 text-xs rounded-md",
                filter === f ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted",
              )}
            >
              {f === "all" ? t("dnd.all", "All") : f === "pc" ? t("dnd.pc", "PC") : t("dnd.npc", "NPC")}
            </button>
          ))}
        </div>
        <Button size="sm" onClick={() => { setShowForm(true); setEditing(false); }}>
          <Plus className="w-3.5 h-3.5 mr-1" />{t("dnd.create", "Create")}
        </Button>
      </div>

      {showForm && (
        <CharacterForm
          token={token}
          defaultType={filter === "all" ? "pc" : filter}
          onDone={onCreated}
          onCancel={() => setShowForm(false)}
        />
      )}

      {editing && selected && (
        <CharacterForm
          token={token}
          defaultType={selected.character_type}
          initial={selected}
          onDone={onUpdated}
          onCancel={() => { setEditing(false); setSelected(null); }}
        />
      )}

      {!characters.length && (
        <p className="text-muted-foreground text-center py-8 text-sm">No characters found</p>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2">
        {characters.map((c) => (
          <div
            key={c.id}
            className="border border-border rounded-lg p-3 hover:bg-muted/30 cursor-pointer"
            onClick={async () => {
              setSelected(await fetchDndCharacter(token, c.id));
              setEditing(true);
            }}
          >
            <div className="flex items-center gap-2 mb-1">
              <span className={cn(
                "text-xs px-1.5 py-0.5 rounded",
                c.character_type === "pc"
                  ? "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300"
                  : "bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300",
              )}>
                {c.character_type === "pc" ? <User className="w-3 h-3 inline mr-0.5" /> : <Users className="w-3 h-3 inline mr-0.5" />}
                {c.character_type.toUpperCase()}
              </span>
              <span className="font-medium">{c.name}</span>
              {c.level > 0 && <span className="text-xs text-muted-foreground">Lv{c.level}</span>}
            </div>
            <div className="flex gap-2 text-xs text-muted-foreground flex-wrap">
              {c.race && <span>{c.race}</span>}
              {c.class_name && <span>{c.class_name}</span>}
              {c.alignment && <span className="ml-auto">{c.alignment}</span>}
            </div>
            {c.character_type === "npc" && (c.personality_traits || c.appearance) && (
              <p className="text-xs text-muted-foreground mt-1 line-clamp-2">
                {c.personality_traits || c.appearance}
              </p>
            )}
            {c.campaign_id && (
              <p className="text-xs text-blue-500 mt-1">Bound to campaign</p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
