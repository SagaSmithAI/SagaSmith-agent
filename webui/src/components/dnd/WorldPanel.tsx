import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Minus, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { DndWorldState, DndCampaignInfo } from "@/lib/types";
import { fetchDndWorldState, fetchDndCampaigns, updateDndFaction, updateDndNpcAttitude } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Props {
  token: string;
}

export function WorldPanel({ token }: Props) {
  const { t } = useTranslation();
  const [campaigns, setCampaigns] = useState<DndCampaignInfo[]>([]);
  const [selectedCid, setSelectedCid] = useState<string>("");
  const [world, setWorld] = useState<DndWorldState | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetchDndCampaigns(token).then(setCampaigns);
  }, [token]);

  const loadWorld = useCallback(async (cid: string) => {
    setLoading(true);
    try {
      setWorld(await fetchDndWorldState(token, cid));
      setSelectedCid(cid);
    } finally {
      setLoading(false);
    }
  }, [token]);

  const adjustFaction = async (name: string, delta: number, note?: string) => {
    await updateDndFaction(token, selectedCid, { faction_name: name, delta, note });
    setWorld(await fetchDndWorldState(token, selectedCid));
  };

  const adjustNpc = async (characterId: string, delta: number, note?: string) => {
    await updateDndNpcAttitude(token, selectedCid, { character_id: characterId, delta, note });
    setWorld(await fetchDndWorldState(token, selectedCid));
  };

  return (
    <div className="space-y-4">
      <select
        className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
        value={selectedCid}
        onChange={(e) => { if (e.target.value) loadWorld(e.target.value); }}
      >
        <option value="">Select a campaign...</option>
        {campaigns.map((c) => (
          <option key={c.id} value={c.id}>{c.name} ({c.status})</option>
        ))}
      </select>

      {loading && <p className="text-muted-foreground text-sm">Loading...</p>}

      {world && (
        <div className="space-y-4">
          {/* Summary */}
          <div className="bg-muted/30 rounded-lg p-3 text-sm">
            <p className="font-medium mb-1">Summary</p>
            <p className="text-muted-foreground">{world.summary}</p>
          </div>

          {/* Factions */}
          <div>
            <h3 className="text-sm font-medium mb-2">Faction Relations</h3>
            {Object.keys(world.factions).length === 0 && (
              <p className="text-xs text-muted-foreground">No faction data yet</p>
            )}
            {Object.entries(world.factions).map(([name, info]) => (
              <div key={name} className="flex items-center justify-between py-1.5 border-b border-border/50 text-sm">
                <span className="font-medium">{name}</span>
                <div className="flex items-center gap-2">
                  <span className={cn(
                    "text-xs px-1.5 py-0.5 rounded",
                    info.attitude === "allied" && "bg-emerald-100 text-emerald-700",
                    info.attitude === "friendly" && "bg-green-100 text-green-700",
                    info.attitude === "neutral" && "bg-gray-100 text-gray-600",
                    info.attitude === "hostile" && "bg-red-100 text-red-700",
                    info.attitude === "vengeful" && "bg-rose-200 text-rose-800",
                  )}>
                    {info.attitude} ({info.score})
                  </span>
                  <button className="p-0.5 hover:bg-muted rounded" onClick={() => adjustFaction(name, -1)}><Minus className="w-3 h-3" /></button>
                  <button className="p-0.5 hover:bg-muted rounded" onClick={() => adjustFaction(name, 1)}><Plus className="w-3 h-3" /></button>
                </div>
              </div>
            ))}
          </div>

          {/* NPCs */}
          <div>
            <h3 className="text-sm font-medium mb-2">Key NPC Status</h3>
            {Object.keys(world.npcs).length === 0 && (
              <p className="text-xs text-muted-foreground">No NPC data yet</p>
            )}
            {Object.entries(world.npcs).map(([cid, info]: [string, any]) => (
              <div key={cid} className="flex items-center justify-between py-1.5 border-b border-border/50 text-sm">
                <div>
                  <span className="font-medium">{info.npc_name || cid}</span>
                  {info.status && <span className="ml-2 text-xs text-muted-foreground">[{info.status}]</span>}
                  {info.location && <span className="ml-2 text-xs text-blue-500">@{info.location}</span>}
                </div>
                <div className="flex items-center gap-2">
                  {info.attitude !== undefined && (
                    <>
                      <span className="text-xs text-muted-foreground">
                        {info.attitude >= 3 ? "Allied" : info.attitude >= 1 ? "Friendly" : info.attitude >= -1 ? "Neutral" : info.attitude >= -3 ? "Hostile" : "Vengeful"}
                        {" "}({info.attitude})
                      </span>
                      <button className="p-0.5 hover:bg-muted rounded" onClick={() => adjustNpc(cid, -1)}><Minus className="w-3 h-3" /></button>
                      <button className="p-0.5 hover:bg-muted rounded" onClick={() => adjustNpc(cid, 1)}><Plus className="w-3 h-3" /></button>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>

          {/* Quests */}
          {world.quests && (
            <div>
              <h3 className="text-sm font-medium mb-2">Quest Progress</h3>
              <div className="grid grid-cols-4 gap-2">
                {(["待触发", "进行中", "完成", "已失败"] as const).map((col) => (
                  <div key={col} className="border border-border rounded p-2 text-xs">
                    <p className="font-medium mb-1 text-muted-foreground">{col}</p>
                    {(world.quests[col] || []).map((q: string) => (
                      <p key={q} className="py-0.5">{q}</p>
                    ))}
                    {(!world.quests[col] || world.quests[col].length === 0) && (
                      <p className="text-muted-foreground/50">—</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
